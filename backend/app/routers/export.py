from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

import os

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Download, ExportStatus, ExportTask, User
from app.schemas.search import SearchFilters
from app.services.export_service import build_filename, new_task_id, run_export
from app.services.subscription_service import ensure_task_slot, get_or_create_subscription
from app.timeutils import utcnow


settings = get_settings()
router = APIRouter(prefix="/api/export", tags=["export"])


@router.post("")
async def create_export(
    payload: dict,
    background: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if user.email_verified_at is None:
        raise HTTPException(status_code=403, detail="Подтвердите email перед выгрузкой")
    sub = get_or_create_subscription(db, user.id)
    try:
        ensure_task_slot(db, sub, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    filters = SearchFilters.model_validate(payload.get("filters") or {})
    limit = int(payload.get("limit") or 0)
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit должен быть > 0")
    if not settings.local_mode and user.credits_balance <= 0:
        raise HTTPException(status_code=402, detail="Недостаточно кредитов. Купите пакет.")
    limit = min(limit, settings.export_max_rows) if settings.local_mode else min(limit, user.credits_balance, settings.export_max_rows)

    task = ExportTask(
        id=new_task_id(),
        user_id=user.id,
        status=ExportStatus.queued,
        progress=0,
        filters_json=filters.model_dump_json(),
        requested_rows=limit,
    )
    db.add(task)
    db.commit()
    background.add_task(run_export, task.id)
    return {"task_id": task.id, "requested_rows": limit}


@router.get("/{task_id}/status")
async def status(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    task = db.get(ExportTask, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    resp: dict = {"status": task.status.value, "progress": task.progress, "error": task.error}
    if task.status == ExportStatus.done and task.download_token:
        resp["download_url"] = f"/api/export/download?token={task.download_token}"
    return resp


@router.get("/download")
async def download(
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    task = db.execute(select(ExportTask).where(ExportTask.download_token == token)).scalar_one_or_none()
    if not task or not task.download_id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    dl = db.get(Download, task.download_id)
    if not dl or not dl.file_path or not os.path.exists(dl.file_path):
        raise HTTPException(status_code=410, detail="Файл удалён или ещё не готов")
    from datetime import datetime
    if dl.expires_at < utcnow():
        raise HTTPException(status_code=410, detail="Срок действия ссылки истёк")
    filters = SearchFilters.model_validate_json(dl.filters_json)
    fname = build_filename(filters)
    return FileResponse(
        path=dl.file_path,
        filename=fname,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
