from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

import json
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

from app.db import get_db
from app.deps import get_current_user
from app.models import Business, ExportStatus, SavedResult, SavedResultItem, SocialAnalysisTask, User
from app.services.social_analysis_service import new_social_task_id, run_social_task
from app.services.subscription_service import ensure_task_slot, get_or_create_subscription, quota_available


router = APIRouter(prefix="/api/social", tags=["social-analysis"])


@router.post("/tasks")
async def create_social_task(
    payload: dict,
    background: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    result_id = int(payload.get("saved_result_id") or 0)
    saved = db.get(SavedResult, result_id)
    if not saved or saved.user_id != user.id:
        raise HTTPException(status_code=404, detail="Сохранённый результат не найден")
    allowed = db.execute(
        select(SavedResultItem.business_id).where(SavedResultItem.saved_result_id == saved.id)
    ).scalars().all()
    requested = payload.get("business_ids") or allowed
    requested_ids = list(dict.fromkeys(int(x) for x in requested))
    allowed_set = set(allowed)
    if not requested_ids or not set(requested_ids).issubset(allowed_set):
        raise HTTPException(status_code=400, detail="Выбраны организации не из этого результата")
    sub = get_or_create_subscription(db, user.id)
    try:
        ensure_task_slot(db, sub, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    available = quota_available(sub, user, "social")
    if len(requested_ids) > available:
        raise HTTPException(
            status_code=402,
            detail=f"Недостаточно квоты анализа соцсетей: выбрано {len(requested_ids)}, доступно {available}",
        )
    task = SocialAnalysisTask(
        id=new_social_task_id(),
        user_id=user.id,
        saved_result_id=saved.id,
        status=ExportStatus.queued,
        business_ids_json=json.dumps(requested_ids),
    )
    db.add(task)
    db.commit()
    background.add_task(run_social_task, task.id)
    return {"task_id": task.id, "org_count": len(requested_ids)}


@router.get("/tasks/{task_id}/status")
async def social_task_status(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    task = db.get(SocialAnalysisTask, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    response = {
        "status": task.status.value,
        "progress": task.progress,
        "message": task.message,
        "error": task.error,
        "org_count": task.org_count,
        "quota_charged": task.quota_charged,
    }
    if task.status == ExportStatus.done:
        response["download_url"] = f"/api/social/download?token={task.download_token}"
        response["result"] = json.loads(task.result_json or "[]")
    return response


@router.get("/tasks")
async def social_tasks(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.execute(
        select(SocialAnalysisTask)
        .where(SocialAnalysisTask.user_id == user.id)
        .order_by(SocialAnalysisTask.created_at.desc())
        .limit(30)
    ).scalars().all()
    return [{
        "id": task.id,
        "saved_result_id": task.saved_result_id,
        "status": task.status.value,
        "progress": task.progress,
        "error": task.error,
        "org_count": task.org_count,
        "created_at": task.created_at.isoformat(),
        "download_url": f"/api/social/download?token={task.download_token}" if task.download_token else None,
    } for task in rows]


@router.get("/download")
async def social_download(token: str = Query(...), db: Session = Depends(get_db)):
    task = db.execute(
        select(SocialAnalysisTask).where(SocialAnalysisTask.download_token == token)
    ).scalar_one_or_none()
    if not task or not task.download_id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    from app.models import Download
    download = db.get(Download, task.download_id)
    if not download or not download.file_path or not os.path.exists(download.file_path):
        raise HTTPException(status_code=410, detail="Файл недоступен")
    return FileResponse(
        download.file_path,
        filename=f"social_analysis_{task.id[:8]}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
