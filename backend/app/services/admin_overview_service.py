from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Local operational aggregates for the admin control center."""

import copy
import threading
import time
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AdminIncident,
    AiRequest,
    BillingPayment,
    CompanyAnalysisTask,
    ExportStatus,
    ExportTask,
    ParserTask,
    ReviewTask,
    SocialAnalysisTask,
    SupportConversation,
    Transaction,
    User,
    UserSubscription,
)
from app.services.admin_incident_service import safe_text
from app.timeutils import utcnow


_CACHE_TTL_SECONDS = 60
_cache_lock = threading.Lock()
_cache_value: Optional[dict] = None
_cache_expires_at = 0.0


def clear_overview_cache() -> None:
    """Test and process-local invalidation hook; no external cache is used."""
    global _cache_value, _cache_expires_at
    with _cache_lock:
        _cache_value = None
        _cache_expires_at = 0.0


def _count(db: Session, model, *conditions) -> int:
    statement = select(func.count()).select_from(model)
    if conditions:
        statement = statement.where(*conditions)
    return int(db.scalar(statement) or 0)


def _task_counts(db: Session, now) -> dict[str, int]:
    models = (ExportTask, ParserTask, ReviewTask, CompanyAnalysisTask, SocialAnalysisTask)
    queued = running = failed_24h = stalled = 0
    for model in models:
        queued += _count(db, model, model.status == ExportStatus.queued)
        running += _count(db, model, model.status == ExportStatus.running)
        updated = getattr(model, "updated_at", model.created_at)
        failed_24h += _count(
            db, model, model.status == ExportStatus.failed, updated >= now - timedelta(hours=24)
        )
        stalled += _count(
            db, model, model.status == ExportStatus.running, updated < now - timedelta(minutes=10)
        )
    return {"queued": queued, "running": running, "failed_24h": failed_24h, "stalled": stalled}


def _integration_state(db: Session, now) -> list[dict]:
    """Infer integration health solely from persisted application signals."""
    signals = []
    parser_failed = _count(
        db, ParserTask,
        ParserTask.status == ExportStatus.failed,
        ParserTask.updated_at >= now - timedelta(hours=24),
    )
    signals.append({
        "name": "Карты", "state": "degraded" if parser_failed else "unknown",
        "signal": "failed_tasks_24h", "value": parser_failed,
    })
    ai_recent = _count(db, AiRequest, AiRequest.created_at >= now - timedelta(days=7))
    signals.append({
        "name": "AI", "state": "ok" if ai_recent else "unknown",
        "signal": "requests_7d", "value": ai_recent,
    })
    payment_failed = _count(
        db, BillingPayment,
        BillingPayment.status.in_(("failed", "canceled")),
        BillingPayment.created_at >= now - timedelta(hours=24),
    ) + _count(
        db, Transaction,
        Transaction.status.in_(("failed", "canceled")),
        Transaction.created_at >= now - timedelta(hours=24),
    )
    signals.append({
        "name": "Платежи", "state": "degraded" if payment_failed else "unknown",
        "signal": "failed_payments_24h", "value": payment_failed,
    })
    email_incidents = _count(
        db, AdminIncident,
        AdminIncident.source == "email",
        AdminIncident.status != "resolved",
    )
    signals.append({
        "name": "Email", "state": "degraded" if email_incidents else "unknown",
        "signal": "open_incidents", "value": email_incidents,
    })
    return signals


def _attention(db: Session, task_counts: dict[str, int]) -> list[dict]:
    items: list[dict] = []
    incidents = db.execute(
        select(AdminIncident)
        .where(AdminIncident.status != "resolved")
        .order_by(AdminIncident.severity.desc(), AdminIncident.last_seen.desc())
        .limit(10)
    ).scalars().all()
    for incident in incidents:
        items.append({
            "kind": "incident", "id": incident.id, "severity": incident.severity,
            "code": safe_text(incident.code), "message": safe_text(incident.message),
            "occurrence_count": incident.occurrence_count,
        })
    if task_counts["stalled"]:
        items.append({
            "kind": "task", "filter": "stalled", "severity": "warning",
            "code": "TASK-STALL", "message": f"Зависших задач: {task_counts['stalled']}",
            "occurrence_count": task_counts["stalled"],
        })
    return items


def build_overview(db: Session, *, now=None) -> dict:
    now = now or utcnow()
    active_24h = db.scalar(
        select(func.count(func.distinct(AiRequest.user_id))).where(
            AiRequest.created_at >= now - timedelta(hours=24)
        )
    ) or 0
    active_7d = db.scalar(
        select(func.count(func.distinct(AiRequest.user_id))).where(
            AiRequest.created_at >= now - timedelta(days=7)
        )
    ) or 0
    plan_rows = db.execute(
        select(UserSubscription.plan_code, func.count(UserSubscription.id))
        .group_by(UserSubscription.plan_code)
    ).all()
    plans = {"free": 0, "pro": 0, "business": 0}
    for code, count in plan_rows:
        if code in plans:
            plans[code] = int(count)
    plans["free"] += _count(
        db,
        User,
        ~User.id.in_(select(UserSubscription.user_id)),
    )
    tasks = _task_counts(db, now)
    succeeded = _count(
        db, BillingPayment,
        BillingPayment.status == "succeeded", BillingPayment.created_at >= now - timedelta(hours=24),
    ) + _count(
        db, Transaction,
        Transaction.status == "succeeded", Transaction.created_at >= now - timedelta(hours=24),
    )
    failed = _count(
        db, BillingPayment,
        BillingPayment.status.in_(("failed", "canceled")),
        BillingPayment.created_at >= now - timedelta(hours=24),
    ) + _count(
        db, Transaction,
        Transaction.status.in_(("failed", "canceled")),
        Transaction.created_at >= now - timedelta(hours=24),
    )
    snapshot = {
        "generated_at": now.isoformat(),
        "users": {
            "active_24h": int(active_24h), "active_7d": int(active_7d),
            "new_7d": _count(db, User, User.created_at >= now - timedelta(days=7)),
        },
        "subscriptions": plans,
        "tasks": tasks,
        "payments": {"succeeded_24h": succeeded, "failed_24h": failed},
        "support": {
            "open": _count(db, SupportConversation, SupportConversation.status == "open"),
            "needs_human": _count(db, SupportConversation, SupportConversation.status == "needs_human"),
        },
        "incidents": {
            "new": _count(db, AdminIncident, AdminIncident.status == "new"),
            "critical": _count(
                db, AdminIncident, AdminIncident.status != "resolved", AdminIncident.severity == "critical"
            ),
        },
        "attention": _attention(db, tasks),
        "integrations": _integration_state(db, now),
    }
    return snapshot


def get_overview(db: Session) -> dict:
    global _cache_value, _cache_expires_at
    now = time.monotonic()
    with _cache_lock:
        if _cache_value is not None and now < _cache_expires_at:
            return copy.deepcopy(_cache_value)
        _cache_value = build_overview(db)
        _cache_expires_at = now + _CACHE_TTL_SECONDS
        return copy.deepcopy(_cache_value)
