from typing import Optional, Any, Dict, List, Literal, Union, Callable

import json
import os
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request
)

from app.config import get_settings
from app.db import get_duck
from app.ratelimit import limiter
from app.schemas.search import OrgPreview, SearchFilters, SearchResponse, SearchStats, TopItem
from app.services.search_service import count_query, preview_query, stats_query, top_n_query


settings = get_settings()
router = APIRouter(prefix="/api", tags=["search"])


def _tree_path() -> Path:
    return Path(os.path.dirname(settings.duckdb_path)) / "category_tree.json"


@router.get("/facets/category-tree")
async def category_tree():
    p = _tree_path()
    if not p.exists():
        # Возвращаем пустое дерево вместо 404, чтобы фронт корректно деградировал.
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Дерево категорий повреждено: {exc}")


@router.post("/search", response_model=SearchResponse)
@limiter.limit("30/minute")
async def search(request: Request, filters: SearchFilters):
    with get_duck() as cur:
        sql_c, params_c = count_query(filters)
        total = cur.execute(sql_c, params_c).fetchone()[0]
        # Статистика по контактам (без has_* фильтров)
        sql_s, params_s = stats_query(filters)
        stats_row = cur.execute(sql_s, params_s).fetchone()
        # Топ-10 по регионам / городам / категориям в текущем сегменте
        sql_tr, params_tr = top_n_query(filters, "region", 10)
        top_regions_rows = cur.execute(sql_tr, params_tr).fetchall()
        sql_tc, params_tc = top_n_query(filters, "city", 10)
        top_cities_rows = cur.execute(sql_tc, params_tc).fetchall()
        sql_tcat, params_tcat = top_n_query(filters, "source_category", 10)
        top_cat_rows = cur.execute(sql_tcat, params_tcat).fetchall()
        # Берём 3 строки превью (после дедупа)
        sql_p, params_p = preview_query(filters, 3)
        rows = cur.execute(sql_p, params_p).fetchall()
    # Порядок колонок должен совпадать с SELECT_COLS в search_service.py
    preview = [
        OrgPreview(
            id=r[0] or 0,
            name=r[1] or "",
            category=r[2] or "",
            region=r[3] or "",
            city=r[4] or "",
            address=r[5] or "",
            phone=r[6] or "",
            website=r[7] or "",
            email=r[8] or "",
            rating=float(r[9] or 0),
            reviews_count=int(r[10] or 0),
            vk=r[11] or "",
            instagram=r[12] or "",
            telegram=r[13] or "",
            whatsapp=r[14] or "",
        )
        for r in rows
    ]
    stats = SearchStats(
        total_in_segment=int(stats_row[0] or 0),
        with_phone=int(stats_row[1] or 0),
        with_website=int(stats_row[2] or 0),
        with_email=int(stats_row[3] or 0),
        with_vk=int(stats_row[4] or 0),
        with_instagram=int(stats_row[5] or 0),
        with_telegram=int(stats_row[6] or 0),
        top_regions=[TopItem(name=r[0] or "", count=int(r[1] or 0)) for r in top_regions_rows],
        top_cities=[TopItem(name=r[0] or "", count=int(r[1] or 0)) for r in top_cities_rows],
        top_categories=[TopItem(name=r[0] or "", count=int(r[1] or 0)) for r in top_cat_rows],
    ) if stats_row else None
    return SearchResponse(total=total, preview=preview, stats=stats)


def _safe_fetch(sql: str, params: Optional[list] = None) -> list:
    """Возвращает [] вместо ошибки, если таблиц ещё нет (CSV не импортированы)."""
    try:
        with get_duck() as cur:
            return cur.execute(sql, params or []).fetchall()
    except Exception:
        return []


@router.get("/facets/regions")
@limiter.limit("60/minute")
async def regions(request: Request):
    rows = _safe_fetch("SELECT region, cnt FROM facets_regions ORDER BY region")
    return [{"region": r[0], "count": r[1]} for r in rows]


@router.get("/facets/cities")
@limiter.limit("60/minute")
async def cities(request: Request, region: Optional[str] = None, q: Optional[str] = None, limit: int = 100):
    sql = "SELECT city, region, cnt FROM facets_cities WHERE 1=1"
    params: list = []
    if region:
        sql += " AND region = ?"
        params.append(region)
    if q:
        sql += " AND city ILIKE ?"
        params.append(q + "%")
    sql += " ORDER BY cnt DESC LIMIT ?"
    params.append(min(limit, 500))
    rows = _safe_fetch(sql, params)
    return [{"city": r[0], "region": r[1], "count": r[2]} for r in rows]


@router.get("/facets/categories")
@limiter.limit("60/minute")
async def categories(request: Request, q: Optional[str] = None, limit: int = 50):
    sql = "SELECT source_category, cnt FROM facets_categories WHERE 1=1"
    params: list = []
    if q:
        sql += " AND source_category ILIKE ?"
        params.append("%" + q + "%")
    sql += " ORDER BY cnt DESC LIMIT ?"
    params.append(min(limit, 500))
    rows = _safe_fetch(sql, params)
    return [{"category": r[0], "count": r[1]} for r in rows]
