from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

"""Скрытая лаборатория Live Index.

Не добавляется в navbar и нужна только для локального/staging просмотра
накопления базы перед заменой основного сценария.
"""

import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Business, BusinessSource, SavedResult, SavedResultItem, User
from app.services.live_index_service import live_index_summary


router = APIRouter(prefix="/api/live-lab", tags=["live-lab"])


@router.get("/summary")
async def summary(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return live_index_summary(db, user_id=user.id)


@router.get("/saved-results")
async def saved_results(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.execute(
        select(SavedResult)
        .where(SavedResult.user_id == user.id)
        .order_by(SavedResult.created_at.desc())
        .limit(50)
    ).scalars().all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "row_count": r.row_count,
            "parser_task_id": r.parser_task_id,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
            "params": _json(r.params_json),
        }
        for r in rows
    ]


@router.get("/saved-results/{result_id}")
async def saved_result_detail(
    result_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    saved = db.get(SavedResult, result_id)
    if not saved or saved.user_id != user.id:
        raise HTTPException(status_code=404, detail="Сохранённый результат не найден")

    rows = db.execute(
        select(SavedResultItem, Business)
        .join(Business, Business.id == SavedResultItem.business_id)
        .where(SavedResultItem.saved_result_id == saved.id)
        .order_by(SavedResultItem.position.asc())
        .limit(200)
    ).all()
    source_rows = db.execute(
        select(BusinessSource).where(
            BusinessSource.business_id.in_([business.id for _, business in rows])
        )
    ).scalars().all() if rows else []
    sources_by_business: dict[tuple[int, str], BusinessSource] = {}
    for source_row in source_rows:
        sources_by_business.setdefault(
            (source_row.business_id, source_row.source.lower()), source_row
        )

    def _source(item: SavedResultItem, business: Business) -> Optional[BusinessSource]:
        return sources_by_business.get((business.id, str(item.source or "").lower()))

    return {
        "id": saved.id,
        "title": saved.title,
        "row_count": saved.row_count,
        "created_at": saved.created_at.isoformat(),
        "params": _json(saved.params_json),
        "items": [
            {
                "position": item.position,
                "source": item.source,
                "source_id": _source(item, b).source_id if _source(item, b) else None,
                "source_url": _source(item, b).source_url if _source(item, b) else None,
                "parsed_at": _source(item, b).last_parsed_at.isoformat() if _source(item, b) and _source(item, b).last_parsed_at else None,
                "business": {
                    "id": b.id,
                    "name": b.name,
                    "category": b.category,
                    "country": b.country,
                    "city": b.city,
                    "region": b.region,
                    "address": b.address,
                    "phone": b.phone,
                    "website": b.website,
                    "social": b.social,
                    "rating": b.rating,
                    "reviews_count": b.reviews_count,
                    "lat": b.lat,
                    "lon": b.lon,
                    "last_seen_at": b.last_seen_at.isoformat() if b.last_seen_at else None,
                    "freshness_score": b.freshness_score,
                    "brand_name": b.brand_name,
                    "chain_key": b.chain_key,
                    "is_franchise": b.is_franchise,
                    "branch_count": b.branch_count,
                },
            }
            for item, b in rows
        ],
    }


def _json(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
