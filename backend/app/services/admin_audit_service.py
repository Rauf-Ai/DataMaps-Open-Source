from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Immutable audit records for administrative user mutations."""

import json

from sqlalchemy.orm import Session

from app.models import AdminAuditLog
from app.services.admin_incident_service import safe_details, safe_text


def write_audit(
    db: Session,
    *,
    admin_id: int,
    action: str,
    target_type: str,
    target_id: Optional[str],
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    comment: str,
    request_id: Optional[str] = None,
) -> AdminAuditLog:
    """Add a sanitized audit row to the current transaction without committing."""
    normalized_comment = str(comment or "").strip()
    if not normalized_comment:
        raise ValueError("Комментарий не может быть пустым")

    effective_request_id = request_id if request_id is not None else db.info.get("admin_request_id")
    audit = AdminAuditLog(
        admin_id=admin_id,
        action=safe_text(str(action)),
        target_type=safe_text(str(target_type)),
        target_id=safe_text(str(target_id)),
        before_json=json.dumps(safe_details(before), ensure_ascii=False) if before is not None else None,
        after_json=json.dumps(safe_details(after), ensure_ascii=False) if after is not None else None,
        comment=safe_text(normalized_comment),
        request_id=safe_text(str(effective_request_id)) if effective_request_id is not None else None,
    )
    db.add(audit)
    return audit
