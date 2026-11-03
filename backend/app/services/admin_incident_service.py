from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Incident grouping and lifecycle operations for the admin control center."""

import hashlib
import json
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminIncident


_GROUPING_WINDOW = timedelta(minutes=15)
_SENSITIVE_KEY = re.compile(
    r"token|secret|password|cookie|authorization|payment_method", re.IGNORECASE
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"\b(?P<key>(?:token|secret|password|cookie|authorization|payment_method)[\w.-]*"
    r"|[A-Za-z_][\w.-]*?(?:token|secret|password|cookie|authorization|payment_method)[\w.-]*)"
    r"(?P<separator>\s*(?:=|:(?!//))\s*)"
    r"(?:(?:Bearer)\s+)?"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^&\s,;}\]\)]+)",
    re.IGNORECASE,
)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_LONG_INTEGER = re.compile(r"\b\d{6,}\b")
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)(?:\?[^\s#]*)")
_DURATION = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:milliseconds?|ms|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b",
    re.IGNORECASE,
)


def safe_text(value: str) -> str:
    """Mask secrets embedded in caller-controlled text without discarding context."""
    if not isinstance(value, str):
        raise TypeError("text must be a string")

    return _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}***", value
    )


def safe_details(value: dict) -> dict:
    """Return a deep-copied details payload with sensitive values masked."""
    if not isinstance(value, dict):
        raise TypeError("details must be a dictionary")

    return _safe_value(value)


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if _SENSITIVE_KEY.search(str(key)) else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        return safe_text(value)
    return value


def _normalize_message(message: str) -> str:
    normalized = _UUID.sub("<uuid>", message)
    normalized = _URL_QUERY.sub(r"\1?<query>", normalized)
    normalized = _LONG_INTEGER.sub("<integer>", normalized)
    return _DURATION.sub("<duration>", normalized)


def _fingerprint(source: str, code: str, message: str) -> str:
    payload = f"{safe_text(source)}|{safe_text(code)}|{_normalize_message(safe_text(message))}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_incident(
    db: Session,
    *,
    code: str,
    source: str,
    message: str,
    severity: str = "warning",
    request_id: Optional[str] = None,
    user_id: Optional[int] = None,
    task_type: Optional[str] = None,
    task_id: Optional[str] = None,
    details: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> AdminIncident:
    """Create an incident or update a matching unresolved incident in its window."""
    recorded_at = now or datetime.utcnow()
    fingerprint = _fingerprint(source, code, message)
    existing = db.scalar(
        select(AdminIncident)
        .where(
            AdminIncident.fingerprint == fingerprint,
            AdminIncident.status != "resolved",
            AdminIncident.last_seen >= recorded_at - _GROUPING_WINDOW,
        )
        .order_by(AdminIncident.last_seen.desc())
        .limit(1)
    )
    if existing is not None:
        existing.occurrence_count += 1
        existing.last_seen = recorded_at
        db.commit()
        db.refresh(existing)
        return existing

    incident = AdminIncident(
        fingerprint=fingerprint,
        code=safe_text(code),
        source=safe_text(source),
        severity=severity,
        status="new",
        message=safe_text(message),
        details_json=json.dumps(safe_details(details)) if details is not None else None,
        request_id=safe_text(request_id) if request_id is not None else None,
        user_id=user_id,
        task_type=safe_text(task_type) if task_type is not None else None,
        task_id=safe_text(task_id) if task_id is not None else None,
        occurrence_count=1,
        first_seen=recorded_at,
        last_seen=recorded_at,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


def update_incident(
    db: Session,
    incident_id: int,
    *,
    status: str,
    admin_id: int,
    comment: str,
) -> AdminIncident:
    """Stage an incident lifecycle update for the caller's transaction.

    The control-center route writes the matching audit row before performing the
    single commit. Keeping commit ownership outside this helper avoids a status
    change surviving an audit failure.
    """
    if status not in {"new", "acknowledged", "resolved"}:
        raise ValueError("Invalid incident status")

    incident = db.get(AdminIncident, incident_id)
    if incident is None:
        raise ValueError("Incident not found")
    if incident.status == "resolved" and status != "resolved":
        raise ValueError("Resolved incident cannot be reopened")

    incident.status = status
    incident.comment = safe_text(comment)
    if status == "acknowledged":
        incident.acknowledged_by = admin_id
    elif status == "resolved":
        incident.resolved_by = admin_id

    return incident
