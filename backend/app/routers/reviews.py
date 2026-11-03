from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

"""Эндпоинты для анализа отзывов: список организаций из выгрузки, создание задачи, статус, скачивание."""

import json
import os
from math import ceil

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
from app.db import get_db, get_duck
from app.deps import get_current_user
from app.models import Download, ExportStatus, ReviewTask, User
from app.services.reviews_parser import extract_org_id
from app.services.reviews_service import new_task_id, run_review_task
from app.services.subscription_service import ensure_task_slot, get_or_create_subscription, quota_available
from app.timeutils import utcnow


settings = get_settings()
router = APIRouter(prefix="/api", tags=["reviews"])


@router.get("/me/downloads/{download_id}/organizations")
async def list_organizations(
    download_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    dl = db.get(Download, download_id)
    if not dl or dl.user_id != user.id:
        raise HTTPException(status_code=404, detail="Выгрузка не найдена")
    if not dl.org_ids_json:
        raise HTTPException(status_code=400, detail="Список организаций недоступен. Пересоздайте выгрузку.")
    try:
        org_ids = json.loads(dl.org_ids_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Список организаций повреждён")
    if not org_ids:
        return []
    placeholders = ",".join(["?"] * len(org_ids))
    sql = (
        f"SELECT id, name, COALESCE(city,''), COALESCE(address,''), "
        f"COALESCE(rating, 0), COALESCE(reviews_count, 0), COALESCE(yandex_card_url, '') "
        f"FROM organizations WHERE id IN ({placeholders})"
    )
    with get_duck() as cur:
        rows = cur.execute(sql, org_ids).fetchall()
    # Сохраняем порядок как в org_ids
    by_id = {int(r[0]): r for r in rows}
    out = []
    for oid in org_ids:
        r = by_id.get(int(oid))
        if not r:
            continue
        out.append({
            "id": int(r[0]),
            "name": r[1] or "",
            "city": r[2] or "",
            "address": r[3] or "",
            "rating": float(r[4]) if r[4] else 0.0,
            "reviews_count": int(r[5]) if r[5] else 0,
            "yandex_card_url": r[6] or "",
        })
    return out


@router.post("/reviews/tasks")
async def create_review_task(
    payload: dict,
    background: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if user.email_verified_at is None:
        raise HTTPException(status_code=403, detail="Подтвердите email перед анализом отзывов")
    stars = payload.get("stars") or {}
    star_min = int(stars.get("min", 1))
    star_max = int(stars.get("max", 5))
    deep = bool(payload.get("deep"))
    if not (1 <= star_min <= star_max <= 5):
        raise HTTPException(status_code=400, detail="Некорректный фильтр звёзд")

    max_orgs = 20 if deep else 50

    # Два режима: либо org_ids из выгрузки, либо произвольные URL'ы Яндекс Карт.
    urls = payload.get("urls") or []
    if urls and isinstance(urls, list):
        # ── Режим «по ссылкам» ───────────────────────────────────────────
        org_ids_int: list[int] = []
        invalid: list[str] = []
        seen: set[int] = set()
        for u in urls:
            if not isinstance(u, str) or not u.strip():
                continue
            oid_str = extract_org_id(u.strip())
            if not oid_str:
                invalid.append(u.strip()[:80])
                continue
            try:
                oid = int(oid_str)
            except ValueError:
                invalid.append(u.strip()[:80])
                continue
            if oid in seen:
                continue
            seen.add(oid)
            org_ids_int.append(oid)
        if not org_ids_int:
            raise HTTPException(
                status_code=400,
                detail="Не удалось распознать ссылки Яндекс Карт. Формат: https://yandex.ru/maps/org/<slug>/<id>/",
            )
        if invalid:
            # Невалидные ссылки сообщим клиенту, но НЕ ронять задачу — продолжим с тем что есть.
            pass
        if len(org_ids_int) > max_orgs:
            raise HTTPException(
                status_code=400,
                detail=f"За раз можно анализировать максимум {max_orgs} организаций"
                       + (" (глубокий режим)" if deep else ""),
            )
    else:
        # ── Режим «из выгрузки» ──────────────────────────────────────────
        download_id = int(payload.get("download_id") or 0)
        org_ids = payload.get("org_ids") or []
        if not org_ids or not isinstance(org_ids, list):
            raise HTTPException(status_code=400, detail="Не выбрано ни одной организации")
        if len(org_ids) > max_orgs:
            raise HTTPException(
                status_code=400,
                detail=f"За раз можно анализировать максимум {max_orgs} организаций"
                       + (" (глубокий режим)" if deep else ""),
            )
        dl = db.get(Download, download_id)
        if not dl or dl.user_id != user.id:
            raise HTTPException(status_code=404, detail="Выгрузка не найдена")
        if not dl.org_ids_json:
            raise HTTPException(status_code=400, detail="Список организаций недоступен")
        allowed = set(json.loads(dl.org_ids_json))
        org_ids_int = [int(x) for x in org_ids]
        if not set(org_ids_int).issubset(allowed):
            raise HTTPException(status_code=400, detail="Выбраны организации не из этой выгрузки")
        invalid = []

    # Грубая оценка: base * N (точная сумма после парсинга)
    sub = get_or_create_subscription(db, user.id)
    try:
        ensure_task_slot(db, sub, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    available = quota_available(sub, user, "reviews")
    if available < len(org_ids_int):
        raise HTTPException(
            status_code=402,
            detail=f"Недостаточно квоты анализа отзывов: нужно {len(org_ids_int)}, доступно {available}",
        )

    task = ReviewTask(
        id=new_task_id(),
        user_id=user.id,
        status=ExportStatus.queued,
        progress=0,
        org_ids_json=json.dumps(org_ids_int),
        star_min=star_min,
        star_max=star_max,
        deep=deep,
    )
    db.add(task)
    db.commit()
    background.add_task(run_review_task, task.id)
    return {
        "task_id": task.id,
        "org_count": len(org_ids_int),
        "quota_required": len(org_ids_int),
        "invalid_urls": invalid,
    }


@router.get("/reviews/tasks/{task_id}/status")
async def review_task_status(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    task = db.get(ReviewTask, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    resp: dict = {
        "status": task.status.value,
        "progress": task.progress,
        "error": task.error,
        "credits_charged": task.credits_charged,
    }
    if task.status == ExportStatus.done and task.download_token:
        resp["download_url"] = f"/api/reviews/download?token={task.download_token}"
    return resp


@router.get("/reviews/tasks")
async def review_tasks_history(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.execute(
        select(ReviewTask)
        .where(ReviewTask.user_id == user.id)
        .order_by(ReviewTask.created_at.desc())
        .limit(20)
    ).scalars().all()
    out = []
    for t in rows:
        try:
            org_count = len(json.loads(t.org_ids_json or "[]"))
        except json.JSONDecodeError:
            org_count = 0
        out.append({
            "id": t.id,
            "status": t.status.value,
            "progress": t.progress,
            "error": t.error,
            "org_count": org_count,
            "star_min": t.star_min,
            "star_max": t.star_max,
            "deep": bool(t.deep),
            "credits_charged": t.credits_charged,
            "created_at": t.created_at.isoformat(),
            "download_url": (
                f"/api/reviews/download?token={t.download_token}"
                if t.status == ExportStatus.done and t.download_token else None
            ),
        })
    return out


@router.get("/reviews/download")
async def download_reviews(
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    task = db.execute(select(ReviewTask).where(ReviewTask.download_token == token)).scalar_one_or_none()
    if not task or not task.download_id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    dl = db.get(Download, task.download_id)
    if not dl or not dl.file_path or not os.path.exists(dl.file_path):
        raise HTTPException(status_code=410, detail="Файл удалён или ещё не готов")
    if dl.expires_at < utcnow():
        raise HTTPException(status_code=410, detail="Срок действия ссылки истёк")
    return FileResponse(
        path=dl.file_path,
        filename=f"reviews_{task.id[:8]}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
