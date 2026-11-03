from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Постоянный Live Index для результатов живого парсинга карт.

Сервис намеренно не знает про стоимость/платежи. Его задача — принять parser
company dict, найти или создать нормализованную организацию, записать источник,
снимок изменений и сохранить пользовательский набор результата.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Business, BusinessSnapshot, BusinessSource, SavedResult, SavedResultItem
from app.services.maps.normalization import absolute_external_url


MATERIAL_FIELDS = (
    "name", "category", "country", "address", "lat", "lon", "phone", "phone_norm",
    "website", "website_norm", "social", "rating", "reviews_count",
    "hours", "services", "features", "price_range", "status", "is_closed",
    "brand_name", "chain_key", "is_franchise", "branch_count",
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"—", "-", "нет", "Нет", "None", "null"}:
        return None
    return text


def _float(value: Any) -> Optional[float]:
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    value = _clean(value)
    if value is None:
        return 0
    digits = re.sub(r"\D+", "", value)
    return int(digits) if digits else 0


def _phone_norm(phone: Any) -> Optional[str]:
    text = _clean(phone)
    if not text:
        return None
    digits = re.sub(r"\D+", "", text)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits if len(digits) >= 7 else None


def _website_norm(site: Any) -> Optional[str]:
    text = _clean(site)
    if not text:
        return None
    first = re.split(r"[\s,;]+", text)[0].strip()
    if not first:
        return None
    url = first if re.match(r"^https?://", first, re.I) else "https://" + first
    parsed = urlparse(url)
    host = (parsed.netloc or parsed.path).lower()
    host = host[4:] if host.startswith("www.") else host
    return host.strip("/") or None


def _source_key(row: dict) -> tuple[str, Optional[str]]:
    raw_source = (_clean(row.get("source")) or "unknown").lower()
    if "янд" in raw_source or "yandex" in raw_source:
        source = "yandex"
    elif "google" in raw_source:
        source = "google"
    elif "2" in raw_source or "gis" in raw_source or "гис" in raw_source:
        source = "2gis"
    else:
        source = raw_source[:32]
    oid = _clean(row.get("oid"))
    return source, oid


def _row_hash(row: dict) -> str:
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _snapshot_data(business: Business) -> dict:
    return {field: getattr(business, field) for field in MATERIAL_FIELDS}


def _changed_fields(existing: Business, incoming: dict) -> list[str]:
    changed = []
    for field in MATERIAL_FIELDS:
        if getattr(existing, field) != incoming.get(field):
            changed.append(field)
    return changed


def _business_payload(row: dict) -> dict:
    site = absolute_external_url(row.get("site") or row.get("website")) or None
    phone = _clean(row.get("phone") or row.get("phones"))
    has_closed = any(
        str(row.get(k) or "").lower().find("закры") >= 0
        for k in ("status", "features", "hours")
    )
    return {
        "name": _clean(row.get("name")) or "Без названия",
        "category": _clean(row.get("category")),
        "country": _clean(row.get("country")),
        "region": _clean(row.get("region")),
        "city": _clean(row.get("city")),
        "address": _clean(row.get("address")),
        "lat": _float(row.get("lat")),
        "lon": _float(row.get("lon")),
        "phone": phone,
        "phone_norm": _phone_norm(phone),
        "website": site,
        "website_norm": _website_norm(site),
        "email": _clean(row.get("email")),
        "social": _clean(row.get("social")),
        "rating": _float(row.get("rating")),
        "reviews_count": _int(row.get("reviews") or row.get("reviews_count")),
        "hours": _clean(row.get("hours")),
        "services": _clean(row.get("services")),
        "features": _clean(row.get("features")),
        "price_range": _clean(row.get("price_range")),
        "brand_name": _clean(row.get("brand_name")),
        "chain_key": _clean(row.get("chain_key")),
        "is_franchise": bool(row.get("is_franchise")),
        "branch_count": max(1, _int(row.get("branch_count")) or 1),
        "status": "closed" if has_closed else "active",
        "is_closed": has_closed,
        "last_seen_at": _now(),
        "last_refreshed_at": _now(),
        "freshness_score": 100,
        "raw_json": json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
    }


def _find_existing_business(db: Session, row: dict, payload: dict) -> Optional[Business]:
    source, source_id = _source_key(row)
    if source_id:
        found = db.execute(
            select(Business)
            .join(BusinessSource, BusinessSource.business_id == Business.id)
            .where(BusinessSource.source == source, BusinessSource.source_id == source_id)
            .limit(1)
        ).scalar_one_or_none()
        if found:
            return found
        # Different IDs from one maps provider identify different branches,
        # even when a franchise shares one website or hotline.
        same_source_ids = db.execute(
            select(BusinessSource.id).where(BusinessSource.source == source).limit(1)
        ).first()
        if same_source_ids:
            name = payload.get("name")
            address = payload.get("address")
            if name and address:
                return db.execute(
                    select(Business).where(Business.name == name, Business.address == address).limit(1)
                ).scalar_one_or_none()
            return None

    name = payload.get("name")
    address = payload.get("address")
    if name and address:
        return db.execute(
            select(Business).where(Business.name == name, Business.address == address).limit(1)
        ).scalar_one_or_none()
    # Website and hotline are weak identities for franchises. Use them only
    # when the incoming card has no physical address.
    if not address and payload.get("website_norm"):
        found = db.execute(
            select(Business).where(Business.website_norm == payload["website_norm"]).limit(1)
        ).scalar_one_or_none()
        if found:
            return found
    if not address and payload.get("phone_norm"):
        return db.execute(
            select(Business).where(Business.phone_norm == payload["phone_norm"]).limit(1)
        ).scalar_one_or_none()
    return None


def upsert_business_from_parser_row(db: Session, row: dict) -> Business:
    payload = _business_payload(row)
    business = _find_existing_business(db, row, payload)
    raw_hash = _row_hash(row)
    source, source_id = _source_key(row)
    source_url = absolute_external_url(row.get("map_url")) or None
    parsed_at = row.get("parsed_at") if isinstance(row.get("parsed_at"), datetime) else _now()

    if business is None:
        business = Business(**payload)
        db.add(business)
        db.flush()
        db.add(BusinessSnapshot(
            business_id=business.id,
            snapshot_json=json.dumps(_snapshot_data(business), ensure_ascii=False, default=str),
            changed_fields_json=json.dumps(["created"], ensure_ascii=False),
        ))
    else:
        changed = _changed_fields(business, payload)
        if changed:
            db.add(BusinessSnapshot(
                business_id=business.id,
                snapshot_json=json.dumps(_snapshot_data(business), ensure_ascii=False, default=str),
                changed_fields_json=json.dumps(changed, ensure_ascii=False),
            ))
        for key, value in payload.items():
            setattr(business, key, value)
        db.flush()

    if source_id:
        bs = db.execute(
            select(BusinessSource)
            .where(BusinessSource.source == source, BusinessSource.source_id == source_id)
            .limit(1)
        ).scalar_one_or_none()
        if bs is None:
            db.add(BusinessSource(
                business_id=business.id,
                source=source,
                source_id=source_id,
                source_url=source_url,
                raw_hash=raw_hash,
                last_parsed_at=parsed_at,
            ))
        else:
            bs.business_id = business.id
            bs.source_url = source_url or bs.source_url
            bs.raw_hash = raw_hash
            bs.last_parsed_at = parsed_at
    return business


def _saved_title(params: dict, fallback: str) -> str:
    searches = params.get("searches") or []
    parts = []
    for s in searches[:3]:
        q = _clean(s.get("query"))
        loc = _clean(s.get("location"))
        if q and loc:
            parts.append(f"{q} · {loc}")
        elif q:
            parts.append(q)
    if len(searches) > 3:
        parts.append(f"ещё {len(searches) - 3}")
    return "; ".join(parts)[:500] or fallback


def create_saved_result_from_rows(
    db: Session,
    *,
    user_id: int,
    parser_task_id: Optional[str],
    title: Optional[str],
    params: dict,
    rows: list[dict],
) -> SavedResult:
    saved = SavedResult(
        user_id=user_id,
        parser_task_id=parser_task_id,
        title=title or _saved_title(params, "Сбор DataMaps"),
        prompt=_clean(params.get("prompt")),
        params_json=json.dumps(params, ensure_ascii=False, sort_keys=True, default=str),
        row_count=0,
    )
    db.add(saved)
    db.flush()

    seen: set[int] = set()
    for pos, row in enumerate(rows, 1):
        business = upsert_business_from_parser_row(db, row)
        if business.id in seen:
            continue
        seen.add(business.id)
        source, _source_id = _source_key(row)
        db.add(SavedResultItem(
            saved_result_id=saved.id,
            business_id=business.id,
            position=pos,
            source=source,
        ))
    saved.row_count = len(seen)
    db.flush()
    return saved


def live_index_summary(db: Session, user_id: Optional[int] = None) -> dict:
    total_businesses = db.execute(select(func.count(Business.id))).scalar_one()
    total_sources = db.execute(select(func.count(BusinessSource.id))).scalar_one()
    total_saved = db.execute(select(func.count(SavedResult.id))).scalar_one()
    user_saved = 0
    if user_id is not None:
        user_saved = db.execute(
            select(func.count(SavedResult.id)).where(SavedResult.user_id == user_id)
        ).scalar_one()

    fresh = db.execute(
        select(func.count(Business.id)).where(Business.freshness_score >= 80)
    ).scalar_one()
    stale = db.execute(
        select(func.count(Business.id)).where(Business.freshness_score < 80)
    ).scalar_one()

    return {
        "total_businesses": int(total_businesses or 0),
        "total_sources": int(total_sources or 0),
        "total_saved_results": int(total_saved or 0),
        "user_saved_results": int(user_saved or 0),
        "fresh_businesses": int(fresh or 0),
        "stale_businesses": int(stale or 0),
    }
