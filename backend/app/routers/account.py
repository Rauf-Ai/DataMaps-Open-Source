from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

import json
import os

from fastapi import (
    APIRouter,
    Depends,
    HTTPException
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import (
    BillingPayment,
    CompanyAnalysisTask,
    Download,
    ExportTask,
    Package,
    ParserTask,
    PromoActivation,
    PromoCode,
    ReviewTask,
    SocialAnalysisTask,
    Transaction,
    User,
)
from app.timeutils import utcnow


router = APIRouter(prefix="/api/me", tags=["account"])


@router.post("/promo")
async def activate_promo(
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    code = str(payload.get("code", "")).strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Введите промокод")
    promo = db.execute(select(PromoCode).where(PromoCode.code == code)).scalar_one_or_none()
    if not promo or not promo.is_active:
        raise HTTPException(status_code=404, detail="Промокод не найден или неактивен")
    if promo.expires_at and promo.expires_at < utcnow():
        raise HTTPException(status_code=400, detail="Срок действия промокода истёк")
    if promo.max_activations > 0 and promo.used_count >= promo.max_activations:
        raise HTTPException(status_code=400, detail="Промокод исчерпан")
    already = db.execute(
        select(PromoActivation).where(PromoActivation.promo_id == promo.id, PromoActivation.user_id == user.id)
    ).scalar_one_or_none()
    if already:
        raise HTTPException(status_code=409, detail="Вы уже активировали этот промокод")
    user.credits_balance += promo.credits
    promo.used_count += 1
    db.add(PromoActivation(promo_id=promo.id, user_id=user.id, credits_granted=promo.credits))
    db.commit()
    return {"ok": True, "credits_granted": promo.credits, "credits_balance": user.credits_balance}


def _download_kind(filters_json: Optional[str]) -> str:
    """Classify downloads so feature-specific files do not leak into exports."""
    if not filters_json:
        return "export"
    try:
        import json as _json
        data = _json.loads(filters_json)
        if isinstance(data, dict):
            if data.get("kind") == "reviews":
                return "reviews"
            if data.get("kind") == "parser":
                return "parser"
            if data.get("kind") == "social":
                return "social"
            if data.get("kind") == "company_analysis":
                return "company_analysis"
    except (ValueError, TypeError):
        pass
    return "export"


@router.get("/downloads")
async def my_downloads(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    kind: Optional[str] = None,
):
    """Список выгрузок пользователя.

    Параметр `kind` фильтрует:
    - `kind=export` (по умолчанию) — обычные выгрузки организаций;
    - `kind=reviews` — только XLSX-файлы анализа отзывов;
    - `kind=all` — всё подряд.
    """
    rows = db.execute(
        select(Download, ExportTask)
        .outerjoin(ExportTask, ExportTask.id == Download.export_task_id)
        .where(Download.user_id == user.id)
        .order_by(Download.created_at.desc())
        .limit(100)
    ).all()
    want = (kind or "export").lower()
    out = []
    for d, t in rows:
        k = _download_kind(d.filters_json)
        if want != "all" and k != want:
            continue
        out.append({
            "id": d.id,
            "row_count": d.row_count,
            "credits_charged": d.credits_charged,
            "created_at": d.created_at.isoformat(),
            "expires_at": d.expires_at.isoformat(),
            "available": bool(d.file_path),
            "export_task_id": d.export_task_id,
            "kind": k,
            "download_url": (
                f"/api/export/download?token={t.download_token}"
                if d.file_path and t and t.download_token else None
            ),
        })
        if len(out) >= 50:
            break
    return out


def _download_history_links(db: Session, download_ids: list[int]) -> dict[int, str]:
    links: dict[int, str] = {}
    if not download_ids:
        return links
    for task in db.execute(select(ExportTask).where(ExportTask.download_id.in_(download_ids))).scalars():
        if task.download_id and task.download_token:
            links[task.download_id] = f"/api/export/download?token={task.download_token}"
    for task in db.execute(select(ParserTask).where(ParserTask.download_id.in_(download_ids))).scalars():
        if task.download_id and task.download_token:
            links[task.download_id] = f"/api/parser/download?token={task.download_token}"
    for task in db.execute(select(ReviewTask).where(ReviewTask.download_id.in_(download_ids))).scalars():
        if task.download_id and task.download_token:
            links[task.download_id] = f"/api/reviews/download?token={task.download_token}"
    for task in db.execute(select(SocialAnalysisTask).where(SocialAnalysisTask.download_id.in_(download_ids))).scalars():
        if task.download_id and task.download_token:
            links[task.download_id] = f"/api/social/download?token={task.download_token}"
    for task in db.execute(
        select(CompanyAnalysisTask).where(
            (CompanyAnalysisTask.xlsx_download_id.in_(download_ids))
            | (CompanyAnalysisTask.zip_download_id.in_(download_ids))
        )
    ).scalars():
        if task.xlsx_download_id and task.xlsx_download_token:
            links[task.xlsx_download_id] = f"/api/parser/company-analysis/download?token={task.xlsx_download_token}&format=xlsx"
        if task.zip_download_id and task.zip_download_token:
            links[task.zip_download_id] = f"/api/parser/company-analysis/download?token={task.zip_download_token}&format=zip"
    return links


_HISTORY_TYPES = {
    "parser": ("search", "Поиск организаций"),
    "export": ("search", "Выгрузка организаций"),
    "company_analysis": ("company_analysis", "Анализ компаний"),
    "reviews": ("company_analysis", "Анализ отзывов"),
    "social": ("company_analysis", "Анализ соцсетей"),
}


@router.get("/download-history")
async def download_history(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.execute(
        select(Download)
        .where(Download.user_id == user.id)
        .order_by(Download.created_at.desc())
        .limit(300)
    ).scalars().all()
    links = _download_history_links(db, [row.id for row in rows])
    items = []
    for row in rows:
        legacy_kind = _download_kind(row.filters_json)
        group, label = _HISTORY_TYPES.get(legacy_kind, ("other", "Другой файл"))
        try:
            filters = json.loads(row.filters_json or "{}")
        except (TypeError, json.JSONDecodeError):
            filters = {}
        extension = os.path.splitext(row.file_path or "")[1].lstrip(".").upper() or "ФАЙЛ"
        if legacy_kind == "company_analysis":
            label = f"Анализ компаний · {extension}"
        available = bool(row.file_path and os.path.isfile(row.file_path) and links.get(row.id))
        items.append({
            "id": row.id,
            "group": group,
            "kind": legacy_kind,
            "label": label,
            "format": extension,
            "row_count": row.row_count,
            "credits_charged": row.credits_charged,
            "created_at": row.created_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "available": available,
            "download_url": links.get(row.id) if available else None,
            "task_id": filters.get("task_id") or row.export_task_id,
        })
    return {"items": items, "total": len(items)}


@router.get("/transactions")
async def my_transactions(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.execute(
        select(Transaction, Package)
        .join(Package, Package.id == Transaction.package_id)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.created_at.desc())
        .limit(50)
    ).all()
    legacy = [
        {
            "id": t.id,
            "package_name": p.name,
            "amount_rub": t.amount_kop / 100,
            "credits": t.credits,
            "status": t.status.value,
            "created_at": t.created_at.isoformat(),
        }
        for t, p in rows
    ]
    billing_rows = db.execute(
        select(BillingPayment)
        .where(BillingPayment.user_id == user.id)
        .order_by(BillingPayment.created_at.desc())
        .limit(50)
    ).scalars().all()
    modern = [
        {
            "id": f"billing-{p.id}",
            "package_name": (
                {"pro": "Подписка Про", "business": "Подписка Бизнес"}.get(p.plan_code)
                if p.kind.value == "subscription" else f"Докупка {p.org_units} организаций"
            ),
            "amount_rub": p.amount_kop / 100,
            "credits": p.org_units,
            "status": p.status,
            "created_at": p.created_at.isoformat(),
        }
        for p in billing_rows
    ]
    return sorted(legacy + modern, key=lambda row: row["created_at"], reverse=True)[:50]
