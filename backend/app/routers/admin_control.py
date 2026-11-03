from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

"""HTTP surface for the isolated admin control center."""

import json
from datetime import date, datetime, time

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_admin_user
from app.models import AdminIncident, User
from app.services.admin_audit_service import write_audit
from app.services.admin_incident_service import safe_details, safe_text, update_incident
from app.services.admin_overview_service import get_overview
from app.services.admin_task_service import (
    AdminTaskDetail,
    AdminTaskRow,
    get_task,
    list_tasks,
    request_cancel,
    retry_task,
)
from app.services.admin_user_service import get_user_360, list_user_audit


router = APIRouter(
    prefix="/api/admin/control", tags=["admin-control"], dependencies=[Depends(get_admin_user)]
)


def _page(page: int, page_size: int) -> None:
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="page должна быть >= 1, page_size - от 1 до 100")


def _comment(payload: dict) -> str:
    comment = safe_text(str(payload.get("comment") or "").strip())
    if not comment:
        raise HTTPException(status_code=400, detail="Комментарий не может быть пустым")
    return comment


def _set_audit_request_id(db: Session, request: Request) -> None:
    db.info["admin_request_id"] = getattr(request.state, "request_id", None)


def _dt(value):
    return value.isoformat() if value else None


def _parse_datetime(value: Optional[str], field: str, *, end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    try:
        if len(value) == 10:
            parsed_date = date.fromisoformat(value)
            return datetime.combine(parsed_date, time.max if end_of_day else time.min)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Некорректное значение {field}") from exc


def _task_row(row: AdminTaskRow | AdminTaskDetail) -> dict:
    result = {
        "type": row.type, "id": row.id, "user_id": row.user_id, "user_email": row.user_email,
        "status": row.status, "progress": row.progress, "source": row.source,
        "requested_count": row.requested_count, "found_count": row.found_count,
        "created_at": _dt(row.created_at), "updated_at": _dt(row.updated_at),
        "error_code": row.error_code, "error_message": row.error_message, "stalled": row.stalled,
    }
    if isinstance(row, AdminTaskDetail):
        result.update({
            "cancel_requested": row.cancel_requested, "priority": row.priority,
            "retry_of_id": row.retry_of_id, "started_at": _dt(row.started_at),
            "finished_at": _dt(row.finished_at),
            "request_id": row.request_id,
        })
    return result


def _incident_row(incident: AdminIncident) -> dict:
    try:
        details = json.loads(incident.details_json or "{}")
    except (TypeError, ValueError):
        details = {}
    return {
        "id": incident.id, "code": incident.code, "source": incident.source,
        "severity": incident.severity, "status": incident.status,
        "message": safe_text(incident.message), "details": safe_details(details if isinstance(details, dict) else {}),
        "request_id": incident.request_id, "user_id": incident.user_id,
        "task_type": incident.task_type, "task_id": incident.task_id,
        "occurrence_count": incident.occurrence_count, "first_seen": _dt(incident.first_seen),
        "last_seen": _dt(incident.last_seen), "acknowledged_by": incident.acknowledged_by,
        "resolved_by": incident.resolved_by, "comment": safe_text(incident.comment or "") or None,
    }


def _map_task_error(error: ValueError) -> HTTPException:
    message = str(error)
    if "не найдена" in message.lower() or "пользователь задачи" in message.lower():
        return HTTPException(status_code=404, detail=message)
    if any(phrase in message.lower() for phrase in ("заверш", "незаверш", "успешную", "повторить нельзя")):
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=400, detail=message)


@router.get("/overview")
def overview(db: Annotated[Session, Depends(get_db)]):
    return get_overview(db)


@router.get("/incidents")
def incidents(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
):
    _page(page, page_size)
    if status and status not in {"new", "acknowledged", "resolved"}:
        raise HTTPException(status_code=400, detail="Неизвестный статус инцидента")
    if severity and severity not in {"info", "warning", "critical"}:
        raise HTTPException(status_code=400, detail="Неизвестная критичность инцидента")
    parsed_from = _parse_datetime(date_from, "date_from")
    parsed_to = _parse_datetime(date_to, "date_to", end_of_day=True)
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from не может быть позже date_to")
    conditions = []
    if status:
        conditions.append(AdminIncident.status == status)
    if severity:
        conditions.append(AdminIncident.severity == severity)
    if source:
        conditions.append(AdminIncident.source == source)
    if q:
        query = f"%{q.strip()}%"
        conditions.append(or_(AdminIncident.code.ilike(query), AdminIncident.request_id.ilike(query)))
    if parsed_from:
        conditions.append(AdminIncident.last_seen >= parsed_from)
    if parsed_to:
        conditions.append(AdminIncident.last_seen <= parsed_to)
    where = and_(*conditions) if conditions else None
    base = select(AdminIncident)
    count_query = select(func.count()).select_from(AdminIncident)
    if where is not None:
        base = base.where(where)
        count_query = count_query.where(where)
    total = int(db.scalar(count_query) or 0)
    rows = db.execute(
        base.order_by(AdminIncident.last_seen.desc(), AdminIncident.id.desc())
        .limit(page_size).offset((page - 1) * page_size)
    ).scalars().all()
    return {"items": [_incident_row(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: int, db: Annotated[Session, Depends(get_db)]):
    incident = db.get(AdminIncident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Инцидент не найден")
    return _incident_row(incident)


@router.patch("/incidents/{incident_id}")
def update_incident_endpoint(
    incident_id: int,
    payload: dict,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(get_admin_user)],
):
    comment = _comment(payload)
    status = str(payload.get("status") or "")
    incident = db.get(AdminIncident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Инцидент не найден")
    before = {"status": incident.status}
    try:
        updated = update_incident(db, incident_id, status=status, admin_id=admin.id, comment=comment)
        write_audit(
            db, admin_id=admin.id, action="incident.status.update", target_type="incident",
            target_id=incident_id, before=before, after={"status": updated.status}, comment=comment,
            request_id=getattr(request.state, "request_id", None),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        status_code = 409 if "cannot be reopened" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc))
    except Exception:
        db.rollback()
        raise
    return _incident_row(updated)


@router.get("/tasks")
def tasks(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    user_query: Optional[str] = None,
    error_code: Optional[str] = None,
    stalled: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    _page(page, page_size)
    try:
        task_page = list_tasks(
            db, page=page, page_size=page_size, status=status, task_type=task_type,
            user_query=user_query, error_code=error_code, stalled=stalled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "items": [_task_row(row) for row in task_page.items], "total": task_page.total,
        "page": task_page.page, "page_size": task_page.page_size,
    }


@router.get("/tasks/{task_type}/{task_id}")
def task_detail(task_type: str, task_id: str, db: Annotated[Session, Depends(get_db)]):
    try:
        return _task_row(get_task(db, task_type, task_id))
    except ValueError as exc:
        raise _map_task_error(exc)


@router.post("/tasks/{task_type}/{task_id}/cancel")
def task_cancel(
    task_type: str, task_id: str, payload: dict, request: Request,
    db: Annotated[Session, Depends(get_db)], admin: Annotated[User, Depends(get_admin_user)],
):
    # Task service owns the atomic state-change plus audit transaction.
    _set_audit_request_id(db, request)
    try:
        return _task_row(request_cancel(db, task_type, task_id, admin.id, _comment(payload)))
    except ValueError as exc:
        raise _map_task_error(exc)


@router.post("/tasks/{task_type}/{task_id}/retry")
def task_retry(
    task_type: str, task_id: str, payload: dict, request: Request,
    db: Annotated[Session, Depends(get_db)], admin: Annotated[User, Depends(get_admin_user)],
):
    # Payment tasks are not part of the task registry and cannot be retried here.
    _set_audit_request_id(db, request)
    try:
        return _task_row(retry_task(db, task_type, task_id, admin.id, _comment(payload)))
    except ValueError as exc:
        raise _map_task_error(exc)


@router.get("/users/{user_id}")
def user_detail(user_id: int, db: Annotated[Session, Depends(get_db)]):
    try:
        return get_user_360(db, user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/users/{user_id}/audit")
def user_audit(
    user_id: int, page: int = 1, page_size: int = 20, db: Session = Depends(get_db)
):
    _page(page, page_size)
    try:
        items, total = list_user_audit(db, user_id, page=page, page_size=page_size)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"items": items, "total": total, "page": page, "page_size": page_size}
