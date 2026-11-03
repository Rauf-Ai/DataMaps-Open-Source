from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Normalized read and control operations for background tasks."""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import String, and_, cast, func, literal, or_, select, union_all, update
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, attributes
from sqlalchemy.orm.exc import ObjectDeletedError

from app.models import (
    AdminIncident,
    CompanyAnalysisTask,
    ExportStatus,
    ExportTask,
    ParserTask,
    ReviewTask,
    SocialAnalysisTask,
    User,
)
from app.services.admin_audit_service import write_audit
from app.services.admin_incident_service import safe_text
from app.timeutils import utcnow


_ERROR_CODE = re.compile(r"^\[([A-Z0-9-]+)\]")
_STALL_AFTER = timedelta(minutes=10)
_TERMINAL_STATUSES = {"done", "failed", "partial", "canceled"}
_TASK_MODELS = {
    "export": ExportTask,
    "parser": ParserTask,
    "review": ReviewTask,
    "company": CompanyAnalysisTask,
    "social": SocialAnalysisTask,
}


def stop_if_cancelled(db: Session, task) -> bool:
    """Finish a cooperative worker without charging its user.

    Workers call this at safe boundaries. Refreshing makes a cancellation made
    by another request visible to a long-running worker using its own session.
    """
    task_id = task.id
    try:
        db.refresh(task)
    except (ObjectDeletedError, InvalidRequestError):
        # Session.refresh() reports a concurrently deleted row as either of
        # these exceptions. Do not hide session/database failures for rows
        # that still exist.
        db.rollback()
        if db.get(type(task), task_id) is None:
            return True
        raise

    status = _status_value(task.status)
    if status == ExportStatus.canceled.value or not bool(task.cancel_requested):
        return status == ExportStatus.canceled.value

    task.status = ExportStatus.canceled
    task.finished_at = task.finished_at or utcnow()
    if hasattr(task, "message"):
        task.message = "Задача отменена"
    db.commit()
    return True


def finalize_task_unless_cancelled(
    db: Session, task, final_status: ExportStatus = ExportStatus.done
) -> bool:
    """Atomically claim task finalization before charging or saving results.

    The successful conditional UPDATE holds SQLite's write lock until the
    caller commits its charge/result transaction. A concurrent cancellation
    uses the inverse conditional UPDATE, so exactly one side can win.
    """
    if final_status not in {ExportStatus.done, ExportStatus.partial}:
        raise ValueError("Финальный статус должен быть done или partial")

    model = type(task)
    finished_at = utcnow()
    result = db.execute(
        update(model)
        .where(
            model.id == task.id,
            model.status == ExportStatus.running,
            model.cancel_requested.is_(False),
        )
        .values(status=final_status, finished_at=finished_at),
        execution_options={"synchronize_session": False, "autoflush": False},
    )
    if result.rowcount == 1:
        attributes.set_committed_value(task, "status", final_status)
        attributes.set_committed_value(task, "finished_at", finished_at)
        return True

    # Do not retain any staged charge/result changes when cancellation won.
    db.rollback()
    current = db.get(model, task.id)
    if current is None:
        return False
    if current.cancel_requested or _status_value(current.status) == ExportStatus.canceled.value:
        current.status = ExportStatus.canceled
        current.finished_at = current.finished_at or utcnow()
        if hasattr(current, "message"):
            current.message = "Задача отменена"
        db.commit()
        return False
    raise RuntimeError(
        f"Задача {task.id} не находится в running при атомарном завершении"
    )


@dataclass(frozen=True)
class AdminTaskRow:
    type: str
    id: str
    user_id: int
    user_email: str
    status: str
    progress: int
    source: Optional[str]
    requested_count: int
    found_count: int
    created_at: datetime
    updated_at: datetime
    error_code: Optional[str]
    error_message: Optional[str]
    stalled: bool


@dataclass(frozen=True)
class AdminTaskDetail(AdminTaskRow):
    cancel_requested: bool
    priority: int
    retry_of_id: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    request_id: Optional[str]


@dataclass(frozen=True)
class TaskPage:
    items: list[AdminTaskRow]
    total: int
    page: int
    page_size: int


def _status_value(status: Any) -> str:
    return status.value if isinstance(status, ExportStatus) else str(status)


def _parse_error(error: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not error:
        return None, None
    message = safe_text(str(error))
    match = _ERROR_CODE.match(message)
    return (match.group(1) if match else "GEN-TASK"), message


def _json_dict(value: Optional[str]) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list_count(value: Optional[str]) -> int:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def _requested_count(task_type: str, input_json: Optional[str], base_count: Optional[int]) -> int:
    if task_type == "parser":
        params = _json_dict(input_json)
        return max(0, int(params.get("target_count") or params.get("max") or 0))
    if task_type in {"review", "company", "social"}:
        return _json_list_count(input_json)
    return max(0, int(base_count or 0))


def _task_union(task_types: Optional[list[str]] = None):
    wanted = task_types or list(_TASK_MODELS)
    branches = []

    def common(model, task_type, source, input_json, requested, found, updated):
        return select(
            literal(task_type).label("type"),
            model.id.label("id"),
            model.user_id.label("user_id"),
            User.email.label("user_email"),
            cast(model.status, String).label("status"),
            model.progress.label("progress"),
            source.label("source"),
            input_json.label("input_json"),
            requested.label("requested_count_base"),
            found.label("found_count"),
            model.error.label("error"),
            model.created_at.label("created_at"),
            updated.label("updated_at"),
            model.cancel_requested.label("cancel_requested"),
            model.priority.label("priority"),
            model.retry_of_id.label("retry_of_id"),
            model.started_at.label("started_at"),
            model.finished_at.label("finished_at"),
        ).join(User, User.id == model.user_id)

    if "export" in wanted:
        branches.append(common(
            ExportTask, "export", literal("catalog"), ExportTask.filters_json,
            ExportTask.requested_rows, literal(0), ExportTask.created_at,
        ))
    if "parser" in wanted:
        branches.append(common(
            ParserTask, "parser", ParserTask.source, ParserTask.params_json,
            literal(0), ParserTask.org_count, ParserTask.updated_at,
        ))
    if "review" in wanted:
        branches.append(common(
            ReviewTask, "review", literal("yandex"), ReviewTask.org_ids_json,
            literal(0), literal(0), ReviewTask.updated_at,
        ))
    if "company" in wanted:
        branches.append(common(
            CompanyAnalysisTask, "company", literal("mixed"),
            CompanyAnalysisTask.business_ids_json, literal(0),
            CompanyAnalysisTask.org_count, CompanyAnalysisTask.updated_at,
        ))
    if "social" in wanted:
        branches.append(common(
            SocialAnalysisTask, "social", literal("social"),
            SocialAnalysisTask.business_ids_json, literal(0),
            SocialAnalysisTask.org_count, SocialAnalysisTask.updated_at,
        ))
    if not branches:
        raise ValueError("Неизвестный тип задачи")
    return union_all(*branches).subquery("admin_tasks")


def list_tasks(
    db: Session,
    *,
    page: int,
    page_size: int,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    user_query: Optional[str] = None,
    error_code: Optional[str] = None,
    stalled: Optional[bool] = None,
) -> TaskPage:
    if page < 1:
        raise ValueError("page должна быть >= 1")
    if page_size < 1 or page_size > 100:
        raise ValueError("page_size должна быть от 1 до 100")
    types = None
    if task_type:
        if task_type not in _TASK_MODELS:
            raise ValueError("Неизвестный тип задачи")
        types = [task_type]
    tasks = _task_union(types)
    conditions = []
    if status:
        if status not in {item.value for item in ExportStatus}:
            raise ValueError("Неизвестный статус задачи")
        conditions.append(tasks.c.status == status)
    if user_query:
        query = f"%{user_query.strip()}%"
        conditions.append(or_(
            tasks.c.user_email.ilike(query),
            cast(tasks.c.user_id, String).ilike(query),
            tasks.c.id.ilike(query),
        ))
    if error_code:
        normalized_code = error_code.strip().upper()
        if normalized_code == "GEN-TASK":
            conditions.append(and_(tasks.c.error.is_not(None), ~tasks.c.error.like("[%")))
        else:
            conditions.append(tasks.c.error.like(f"[{normalized_code}]%"))
    if stalled is not None:
        stale_before = utcnow() - _STALL_AFTER
        stalled_condition = and_(
            tasks.c.status == ExportStatus.running.value,
            func.coalesce(tasks.c.updated_at, tasks.c.created_at) < stale_before,
        )
        conditions.append(stalled_condition if stalled else ~stalled_condition)
    where = and_(*conditions) if conditions else None
    count_query = select(func.count()).select_from(tasks)
    data_query = select(tasks)
    if where is not None:
        count_query = count_query.where(where)
        data_query = data_query.where(where)
    total = int(db.scalar(count_query) or 0)
    records = db.execute(
        data_query.order_by(tasks.c.created_at.desc(), tasks.c.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).mappings().all()
    now = utcnow()
    items = []
    for record in records:
        code, message = _parse_error(record["error"])
        updated_at = record["updated_at"] or record["created_at"]
        items.append(AdminTaskRow(
            type=record["type"],
            id=record["id"],
            user_id=record["user_id"],
            user_email=record["user_email"],
            status=_status_value(record["status"]),
            progress=int(record["progress"] or 0),
            source=record["source"],
            requested_count=_requested_count(
                record["type"], record["input_json"], record["requested_count_base"]
            ),
            found_count=int(record["found_count"] or 0),
            created_at=record["created_at"],
            updated_at=updated_at,
            error_code=code,
            error_message=message,
            stalled=_status_value(record["status"]) == "running"
            and updated_at < now - _STALL_AFTER,
        ))
    return TaskPage(items=items, total=total, page=page, page_size=page_size)


def _get_model(task_type: str):
    model = _TASK_MODELS.get(task_type)
    if model is None:
        raise ValueError("Неизвестный тип задачи")
    return model


def _source(task_type: str, task) -> str:
    return task.source if task_type == "parser" else {
        "export": "catalog", "review": "yandex", "company": "mixed", "social": "social"
    }[task_type]


def _input_json(task_type: str, task) -> str:
    return {
        "export": task.filters_json if task_type == "export" else None,
        "parser": task.params_json if task_type == "parser" else None,
        "review": task.org_ids_json if task_type == "review" else None,
        "company": task.business_ids_json if task_type == "company" else None,
        "social": task.business_ids_json if task_type == "social" else None,
    }[task_type]


def get_task(db: Session, task_type: str, task_id: str) -> AdminTaskDetail:
    model = _get_model(task_type)
    task = db.get(model, task_id)
    if task is None:
        raise ValueError("Задача не найдена")
    user = db.get(User, task.user_id)
    if user is None:
        raise ValueError("Пользователь задачи не найден")
    error_code, error_message = _parse_error(task.error)
    last_incident_request_id = db.scalar(
        select(AdminIncident.request_id)
        .where(
            AdminIncident.task_type == task_type,
            AdminIncident.task_id == task.id,
        )
        .order_by(AdminIncident.last_seen.desc(), AdminIncident.id.desc())
        .limit(1)
    )
    updated_at = getattr(task, "updated_at", None) or task.created_at
    if task_type == "export":
        requested_base = task.requested_rows
        found_count = 0
    else:
        requested_base = 0
        found_count = int(getattr(task, "org_count", 0) or 0)
    return AdminTaskDetail(
        type=task_type,
        id=task.id,
        user_id=task.user_id,
        user_email=user.email,
        status=_status_value(task.status),
        progress=int(task.progress or 0),
        source=_source(task_type, task),
        requested_count=_requested_count(task_type, _input_json(task_type, task), requested_base),
        found_count=found_count,
        created_at=task.created_at,
        updated_at=updated_at,
        error_code=error_code,
        error_message=error_message,
        stalled=_status_value(task.status) == "running" and updated_at < utcnow() - _STALL_AFTER,
        cancel_requested=bool(task.cancel_requested),
        priority=int(task.priority or 0),
        retry_of_id=task.retry_of_id,
        started_at=task.started_at,
        finished_at=task.finished_at,
        request_id=last_incident_request_id,
    )


def _required_comment(comment: str) -> str:
    normalized = safe_text(str(comment or "").strip())
    if not normalized:
        raise ValueError("Комментарий не может быть пустым")
    return normalized


def request_cancel(
    db: Session, task_type: str, task_id: str, admin_id: int, comment: str
) -> AdminTaskDetail:
    model = _get_model(task_type)
    comment = _required_comment(comment)
    task = db.get(model, task_id)
    if task is None:
        raise ValueError("Задача не найдена")
    status = _status_value(task.status)
    if status in _TERMINAL_STATUSES:
        raise ValueError("Завершённую задачу отменить нельзя")
    before = {"status": status, "cancel_requested": bool(task.cancel_requested)}
    try:
        after_status = ExportStatus.canceled if status == "queued" else ExportStatus.running
        finished_at = utcnow() if status == "queued" else None
        result = db.execute(
            update(model)
            .where(
                model.id == task.id,
                model.status == task.status,
                model.cancel_requested.is_(False),
            )
            .values(
                cancel_requested=True,
                status=after_status,
                finished_at=finished_at,
            ),
            execution_options={"synchronize_session": False, "autoflush": False},
        )
        if result.rowcount != 1:
            db.rollback()
            current = db.get(model, task_id)
            if current is None:
                raise ValueError("Задача не найдена")
            if _status_value(current.status) in _TERMINAL_STATUSES:
                raise ValueError("Завершённую задачу отменить нельзя")
            raise ValueError("Отмена задачи уже запрошена")
        attributes.set_committed_value(task, "cancel_requested", True)
        attributes.set_committed_value(task, "status", after_status)
        attributes.set_committed_value(task, "finished_at", finished_at)
        write_audit(
            db,
            admin_id=admin_id,
            action="task.cancel.request",
            target_type=f"task.{task_type}",
            target_id=task.id,
            before=before,
            after={
                "status": after_status.value,
                "cancel_requested": True,
            },
            comment=comment,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_task(db, task_type, task.id)


def _clone_for_retry(task_type: str, original):
    common = {
        "id": str(uuid.uuid4()),
        "user_id": original.user_id,
        "status": ExportStatus.queued,
        "progress": 0,
        "retry_of_id": original.id,
    }
    if task_type == "export":
        return ExportTask(
            **common, requested_rows=original.requested_rows, filters_json=original.filters_json
        )
    if task_type == "parser":
        return ParserTask(
            **common, kind=original.kind, source=original.source,
            params_json=original.params_json,
        )
    if task_type == "review":
        return ReviewTask(
            **common, org_ids_json=original.org_ids_json, star_min=original.star_min,
            star_max=original.star_max, deep=original.deep,
        )
    if task_type == "company":
        return CompanyAnalysisTask(
            **common, saved_result_id=original.saved_result_id,
            chat_session_id=original.chat_session_id,
            business_ids_json=original.business_ids_json,
            options_json=original.options_json,
            message="Повторный анализ поставлен в очередь",
        )
    return SocialAnalysisTask(
        **common, saved_result_id=original.saved_result_id,
        business_ids_json=original.business_ids_json,
    )


def retry_task(
    db: Session, task_type: str, task_id: str, admin_id: int, comment: str
) -> AdminTaskDetail:
    model = _get_model(task_type)
    comment = _required_comment(comment)
    original = db.get(model, task_id)
    if original is None:
        raise ValueError("Задача не найдена")
    status = _status_value(original.status)
    if status in {"queued", "running"}:
        raise ValueError("Незавершённую задачу повторить нельзя")
    if status == "done" and not original.error:
        raise ValueError("Успешную задачу без ошибки повторить нельзя")
    clone = _clone_for_retry(task_type, original)
    try:
        db.add(clone)
        write_audit(
            db,
            admin_id=admin_id,
            action="task.retry",
            target_type=f"task.{task_type}",
            target_id=original.id,
            before={"status": status, "error_code": _parse_error(original.error)[0]},
            after={"new_task_id": clone.id, "status": "queued", "retry_of_id": original.id},
            comment=comment,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_task(db, task_type, clone.id)
