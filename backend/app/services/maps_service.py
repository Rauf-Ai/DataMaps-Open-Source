from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Оркестратор живого парсинга карт (запускается из BackgroundTasks).

По образцу app.services.reviews_service: модель ParserTask → фоновая корутина →
запись Download с токеном (для файлов) или result_json (для enrich).

Виды задач (ParserTask.kind):
  - live_search : поиск организаций по запросу+городу → XLSX.
  - enrich      : обогащение одной карточки → result_json.
  - org_zip     : сбор отзывов/фото/акций → ZIP с промтом лендинга.
"""

import asyncio
import json
import logging
import os
import threading
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.models import Download, ExportStatus, ParserTask, User
from app.security import generate_token
from app.services.admin_incident_service import record_incident, safe_text
from app.services.admin_task_service import finalize_task_unless_cancelled, stop_if_cancelled
from app.services.live_index_service import create_saved_result_from_rows
from app.services.parser_task_results import (
    append_task_results,
    replace_task_results,
    task_result_identity,
)
from app.services.subscription_service import get_or_create_subscription, quota_available, spend_quota
from app.services.business_identity import annotate_franchise_groups, deduplicate_cards, filter_franchises
from app.services.maps import xlsx, yandex_search
from app.services.maps.geography import apply_geography, resolve_location
from app.services.maps.socials import NETWORK_DOMAINS, has_social_network
from app.timeutils import utcnow

log = logging.getLogger(__name__)
settings = get_settings()


def new_task_id() -> str:
    return str(uuid.uuid4())


def _record_parser_failure(db, task_id: str, exc: Exception) -> None:
    db.rollback()
    task = db.get(ParserTask, task_id)
    if not task:
        return
    task.status = ExportStatus.failed
    task.finished_at = utcnow()
    task.error = f"[PARSER-500] {type(exc).__name__}: {safe_text(str(exc))[:400]}"
    record_incident(
        db,
        code="PARSER-500",
        source="maps_service",
        message=f"{type(exc).__name__} while running parser task",
        severity="critical",
        user_id=task.user_id,
        task_type="parser",
        task_id=task.id,
        details={"exception_type": type(exc).__name__, "kind": task.kind},
    )


def map_source_configuration_error(params: dict, config=None) -> Optional[dict]:
    """Return a stable user-facing error when a selected map source is not configured."""
    config = config or settings
    searches = params.get("searches") or []
    sources = {
        str(row.get("source") or params.get("source") or "yandex").lower()
        for row in searches
        if isinstance(row, dict)
    } or {str(params.get("source") or "yandex").lower()}

    if "yandex" in sources and not config.yandex_search_api_key:
        return {
            "code": "MAP-201",
            "message": "Поиск Яндекс Карт не настроен",
            "hint": "На сервере отсутствует YANDEX_SEARCH_API_KEY. Добавьте ключ в .env и перезапустите backend.",
        }
    if "2gis" in sources and not config.twogis_api_key:
        return {
            "code": "MAP-202",
            "message": "Поиск 2ГИС не настроен",
            "hint": "На сервере отсутствует TWOGIS_API_KEY. Добавьте ключ в .env и перезапустите backend.",
        }
    if "both" in sources and not (config.yandex_search_api_key or config.twogis_api_key):
        return {
            "code": "MAP-203",
            "message": "Не настроен ни один источник карт",
            "hint": "Добавьте YANDEX_SEARCH_API_KEY или TWOGIS_API_KEY в .env и перезапустите backend.",
        }
    return None


def _task_error_text(detail: dict) -> str:
    hint = f" Подсказка: {detail['hint']}" if detail.get("hint") else ""
    return f"[{detail['code']}] {detail['message']}.{hint}"


# ---------------------------------------------------------------------------
# Сбор по источникам
# ---------------------------------------------------------------------------
def _tri(v) -> str:
    v = str(v or "any").lower()
    return v if v in ("any", "yes", "no") else "any"


def _make_filter_fn(params: dict):
    """Общие фильтры: телефон/сайт (any/yes/no), обязательные соцсети (по сетям),
    отзывы от/до, рейтинг от/до. Возвращает None, если фильтров нет."""
    f_phone = _tri(params.get("f_phone"))
    f_site = _tri(params.get("f_site"))
    social_nets = [s for s in (params.get("social_nets") or []) if s in NETWORK_DOMAINS]
    try:
        reviews_min = int(params.get("reviews_min") or 0)
    except (TypeError, ValueError):
        reviews_min = 0
    reviews_max = params.get("reviews_max")
    try:
        reviews_max = int(reviews_max) if reviews_max not in (None, "", 0) else None
    except (TypeError, ValueError):
        reviews_max = None
    try:
        rating_min = float(params.get("rating_min") or 0)
    except (TypeError, ValueError):
        rating_min = 0.0
    try:
        rating_max = float(params.get("rating_max") if params.get("rating_max") not in (None, "") else 5)
    except (TypeError, ValueError):
        rating_max = 5.0

    if (f_phone == "any" and f_site == "any" and not social_nets
            and reviews_min <= 0 and reviews_max is None and rating_min <= 0 and rating_max >= 5):
        return None

    def _has(v) -> bool:
        return v not in (None, "", "—")

    def _fn(c: dict) -> bool:
        if f_phone == "yes" and not _has(c.get("phone")):
            return False
        if f_phone == "no" and _has(c.get("phone")):
            return False
        if f_site == "yes" and c.get("has_site") != "Да":
            return False
        if f_site == "no" and c.get("has_site") == "Да":
            return False
        if social_nets:
            for net in social_nets:
                if not has_social_network(c.get("social") or "", net):
                    return False
        n_rev = int(c.get("reviews") or 0)
        if reviews_min > 0 and n_rev < reviews_min:
            return False
        if reviews_max is not None and n_rev > reviews_max:
            return False
        if rating_min > 0 or rating_max < 5:
            try:
                rv = float(c.get("rating"))
            except (TypeError, ValueError):
                rv = 0.0
            if rv < rating_min or rv > rating_max:
                return False
        return True

    return _fn


def _collect(source: str, query: str, max_orgs: int, emit, filter_fn, country: str = "") -> list[dict]:
    """Синхронный сбор по выбранному источнику. Вызывается в asyncio.to_thread."""
    if source == "yandex":
        rows = yandex_search.collect(query, max_orgs, emit=emit, filter_fn=filter_fn)
        for r in rows:
            r["source"] = "Яндекс"
        return rows
    if source == "2gis":
        try:
            from app.services.maps import twogis_search
        except ImportError:
            return []
        rows = twogis_search.collect(query, max_orgs, emit=emit, filter_fn=filter_fn)
        for r in rows:
            r["source"] = "2ГИС"
        return rows
    if source == "google":
        try:
            from app.services.maps import google_search
        except ImportError:
            return []
        rows = google_search.collect(query, max_orgs, emit=emit, filter_fn=filter_fn, country=country)
        for r in rows:
            r["source"] = "Google"
        return rows
    if source == "both":
        half = max(1, max_orgs // 2)
        ya = yandex_search.collect(query, half, emit=emit, filter_fn=filter_fn)
        for r in ya:
            r["source"] = "Яндекс"
        tg: list[dict] = []
        try:
            from app.services.maps import twogis_search
            tg = twogis_search.collect(query, max_orgs - len(ya), emit=emit, filter_fn=filter_fn)
            for r in tg:
                r["source"] = "2ГИС"
        except ImportError:
            pass
        # Дедуп по (name, address)
        seen: set[tuple] = set()
        merged: list[dict] = []
        for c in ya + tg:
            key = (c.get("name", "").lower(), c.get("address", "").lower())
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)
        return merged[:max_orgs]
    return []


def _compose_map_query(query: str, location: str) -> str:
    """Append a location only when it is not already present in the query."""
    query = " ".join(str(query or "").split())
    location = " ".join(str(location or "").split())
    if not location:
        return query
    normalized_query = query.casefold().replace("ё", "е")
    normalized_location = location.casefold().replace("ё", "е")
    if normalized_location in normalized_query:
        return query
    return f"{query} {location}".strip()


def _apply_job_geography(row: dict, geography: dict) -> dict:
    """Fill provider geography from one resolved search location and timestamp it."""
    item = apply_geography(row, geography)
    item.setdefault("parsed_at", utcnow())
    return item


def _progress_organization(row: dict, source: str) -> dict:
    source_label = {
        "yandex": "Яндекс Карты",
        "google": "Google Maps",
        "2gis": "2ГИС",
        "both": "Карты",
    }.get(source, source)
    return {
        "id": str(row.get("oid") or row.get("id") or ""),
        "name": str(row.get("name") or "Без названия")[:120],
        "city": str(row.get("city") or row.get("region") or "")[:100],
        "rating": row.get("rating"),
        "source": str(row.get("source") or source_label),
        "source_url": str(row.get("map_url") or ""),
    }


def _collect_jobs(
    jobs: list[dict],
    target_count: int,
    emit,
    filter_fn,
    franchise_mode: str = "any",
) -> list[dict]:
    """Collect mixed-source variants until the final unique target is reached."""
    collected: list[dict] = []
    recent_organizations: list[dict] = []
    recent_keys: set[str] = set()
    live_match_keys: set[str] = set()
    checked_total = 0
    total_est = max(1, target_count)
    for job in jobs:
        q = str(job.get("query") or "").strip()
        loc = str(job.get("location") or "").strip()
        country = str(job.get("country") or "").strip()
        geography = resolve_location(loc, str(job.get("country") or ""))
        source = str(job.get("source") or "yandex").lower()
        full = _compose_map_query(q, loc)

        job_checked = 0

        def job_emit(event=None, _q=q, _loc=loc, _source=source, **kw):
            nonlocal job_checked
            job_checked = max(job_checked, int(kw.get("checked") or 0))
            ekw = {"found": min(target_count, len(live_match_keys)), "total": total_est}
            ekw["checked_count"] = checked_total + job_checked
            organization = kw.get("organization")
            if isinstance(organization, dict):
                live_item = _apply_job_geography(organization, geography)
                live_item.setdefault("source", {
                    "yandex": "Яндекс",
                    "google": "Google",
                    "2gis": "2ГИС",
                }.get(_source, _source))
                live_item.setdefault("matched_query", _q)
                compact = _progress_organization(live_item, _source)
                identity = compact["id"] or f"{compact['name'].casefold()}|{compact['city'].casefold()}"
                live_match_keys.add(identity)
                ekw["found"] = min(target_count, len(live_match_keys))
                if event == "match":
                    # The background pump persists this full card immediately;
                    # compact data below remains solely for legacy status polls.
                    ekw["organization"] = dict(live_item)
                if identity not in recent_keys:
                    recent_keys.add(identity)
                    recent_organizations.append(compact)
                    del recent_organizations[:-8]
                ekw["recent_organizations"] = list(recent_organizations)
            m = kw.get("message")
            if m:
                ekw["message"] = (f"[{_q} · {_loc}] {m}" if _loc else f"[{_q}] {m}")[:300]
            emit(**ekw)

        try:
            per_query_limit = min(120, max(target_count, target_count - len(collected)))
            rows = _collect(source, full, per_query_limit, job_emit, filter_fn, country=country)
        except Exception as exc:  # noqa: BLE001 — одна строка застряла, не рушим весь сбор
            log.warning("collect job failed (%s / %s): %s", q, loc, exc)
            rows = []
        checked_total += job_checked
        for row in rows:
            item = _apply_job_geography(row, geography)
            item["matched_query"] = q
            collected.append(item)
        unique = annotate_franchise_groups(deduplicate_cards(collected))
        visible = filter_franchises(unique, franchise_mode)
        emit(
            found=min(len(visible), target_count),
            total=total_est,
            checked_count=checked_total,
            recent_organizations=list(recent_organizations),
            message=f"Уникальных: {len(visible)}",
        )
        if len(visible) >= target_count:
            return visible[:target_count]
    unique = annotate_franchise_groups(deduplicate_cards(collected))
    visible = filter_franchises(unique, franchise_mode)
    emit(
        found=min(len(visible), target_count),
        total=total_est,
        checked_count=checked_total,
        recent_organizations=list(recent_organizations),
        message=f"Собрано: {len(visible)}",
    )
    return visible[:target_count]


# ---------------------------------------------------------------------------
# live_preview — бесплатный образец (1–10 организаций), без списания и файла
# ---------------------------------------------------------------------------
async def _run_preview(db, task: ParserTask, user: User) -> None:
    if stop_if_cancelled(db, task):
        return
    params = json.loads(task.params_json or "{}")
    raw = params.get("searches") or [{"query": params.get("query"),
                                      "location": params.get("location") or params.get("city")}]
    first = raw[0] if raw else {}
    q = (first.get("query") or "").strip()
    loc = (first.get("location") or "").strip()
    if not q:
        raise RuntimeError("Пустой поисковый запрос")
    max_per = max(1, min(int(params.get("max", 50)), 120))
    n = max(1, min(10, round(max_per / 12) or 1))  # 1–10 пропорционально
    first_loc = next((x.strip() for x in loc.split(",") if x.strip()), "")
    full = f"{q} {first_loc}".strip() if first_loc else q
    filter_fn = _make_filter_fn(params)

    holder = {"found": 0, "total": n, "message": "Предпросмотр…"}
    lock = threading.Lock()

    def emit(event=None, **kw):
        with lock:
            for k in ("found", "total", "message"):
                if k in kw:
                    holder[k] = kw[k]

    coro = asyncio.to_thread(_collect, task.source, full, n, emit, filter_fn)
    ct = asyncio.ensure_future(coro)
    while not ct.done():
        await asyncio.sleep(2)
        if stop_if_cancelled(db, task):
            return
        with lock:
            found, total, msg = holder["found"], holder["total"], holder["message"]
        task.progress = min(95, int(found * 100 / total) if total else 0)
        task.message = msg[:300] if msg else None
        db.commit()
    try:
        rows = await ct
    except Exception:  # noqa: BLE001
        rows = []

    if not finalize_task_unless_cancelled(db, task, ExportStatus.done):
        return
    task.result_json = json.dumps({"rows": rows[:n]}, ensure_ascii=False)
    task.org_count = len(rows)
    task.progress = 100
    task.message = f"Предпросмотр: {len(rows)}"
    db.commit()


# ---------------------------------------------------------------------------
# live_search
# ---------------------------------------------------------------------------
async def _run_live_search(db, task: ParserTask, user: User) -> None:
    if stop_if_cancelled(db, task):
        return
    params = json.loads(task.params_json or "{}")
    config_error = map_source_configuration_error(params)
    if config_error:
        task.status = ExportStatus.failed
        task.progress = 0
        task.error = _task_error_text(config_error)
        task.message = config_error["message"]
        db.commit()
        return
    # Строки поиска: [{query, location}]. Back-compat: одиночные query/location.
    raw_searches = params.get("searches")
    if not raw_searches:
        raw_searches = [{"query": params.get("query"), "location": params.get("location") or params.get("city")}]
    target_count = max(1, min(int(params.get("target_count") or params.get("max", 50)), 5000))

    jobs: list[dict] = []
    for s in raw_searches:
        q = (s.get("query") or "").strip()
        loc = (s.get("location") or "").strip()
        if not q:
            continue
        # локация может содержать несколько мест через запятую
        for one in ([x.strip() for x in loc.split(",") if x.strip()] or [""]):
            jobs.append({
                "query": q,
                "location": one,
                "country": s.get("country") or "",
                "source": s.get("source") or task.source,
            })
    if not jobs:
        raise RuntimeError("Пустой поисковый запрос")

    filter_fn = _make_filter_fn(params)

    # Прогресс через потокобезопасный holder + асинхронный pump.
    holder = {
        "found": 0,
        "total": target_count,
        "checked_count": 0,
        "recent_organizations": [],
        "message": "Запуск…",
    }
    pending_result_rows: list[dict] = []
    pending_result_keys: set[str] = set()
    lock = threading.Lock()

    def emit(event=None, **kw):
        with lock:
            for k in ("found", "total", "checked_count", "recent_organizations", "message"):
                if k in kw:
                    holder[k] = kw[k]
            organization = kw.get("organization")
            if event == "match" and isinstance(organization, dict):
                row = dict(organization)
                identity_key = task_result_identity(row)
                if identity_key not in pending_result_keys:
                    pending_result_keys.add(identity_key)
                    pending_result_rows.append(row)

    collect_coro = asyncio.to_thread(
        _collect_jobs,
        jobs,
        target_count,
        emit,
        filter_fn,
        _tri(params.get("f_franchise")),
    )
    collect_task = asyncio.ensure_future(collect_coro)
    while not collect_task.done():
        await asyncio.sleep(2)
        if stop_if_cancelled(db, task):
            return
        with lock:
            found, total, msg = holder["found"], holder["total"], holder["message"]
            pending_rows = list(pending_result_rows)
            pending_result_rows.clear()
            snapshot = {
                "found_count": found,
                "target_count": total,
                "checked_count": holder["checked_count"],
                "recent_organizations": list(holder["recent_organizations"]),
            }
        if pending_rows:
            append_task_results(db, task.id, pending_rows)
        task.progress = min(95, int(found * 100 / total) if total else 0)
        task.org_count = found
        task.progress_json = json.dumps(snapshot, ensure_ascii=False)
        task.message = msg[:300] if msg else None
        db.commit()
    companies = await collect_task
    with lock:
        final_snapshot = {
            "found_count": len(companies),
            "target_count": target_count,
            "checked_count": holder["checked_count"],
            "recent_organizations": list(holder["recent_organizations"]),
        }
    task.progress_json = json.dumps(final_snapshot, ensure_ascii=False)
    # Do not keep a write transaction open while producing the XLSX below.
    db.commit()

    if not companies:
        replace_task_results(db, task.id, companies)
        task.status = ExportStatus.failed
        source_names = sorted({str(job.get("source") or task.source) for job in jobs})
        src_name = ", ".join({"yandex": "Яндекс Карты", "google": "Google Maps"}.get(value, value) for value in source_names)
        has_filters = filter_fn is not None
        task.error = (
            f"[E1] Ничего не собрано ({src_name}). Кредиты не списаны. Возможные причины: "
            + ("① слишком строгие фильтры (телефон/сайт/соцсети/рейтинг/отзывы) — ослабьте их; " if has_filters else "")
            + "② источник не знает эту локацию — Яндекс Карты хорошо ищут по России/СНГ, "
            "для других стран выбирайте Google Maps; "
            "③ опечатка в запросе или локации; "
            "④ временная блокировка/капча источника — повторите позже."
        )
        db.commit()
        return

    if stop_if_cancelled(db, task):
        return
    sub = get_or_create_subscription(db, user.id)
    available = quota_available(sub, user, "organizations")
    if available < len(companies):
        replace_task_results(db, task.id, companies)
        task.status = ExportStatus.failed
        task.error = (
            f"[BILL-402] Недостаточно доступных организаций: нужно {len(companies)}, есть {available}. "
            f"Собрано {len(companies)} организаций, но файл не сохранён — уменьшите объём или докупите организации."
        )
        db.commit()
        return

    os.makedirs(settings.export_dir, exist_ok=True)
    file_path = Path(settings.export_dir) / f"parser_{task.id}.xlsx"
    await asyncio.to_thread(xlsx.write_companies_xlsx, file_path, companies)

    if stop_if_cancelled(db, task):
        return
    if not finalize_task_unless_cancelled(db, task, ExportStatus.done):
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    # Streaming may include raw duplicates or rows later excluded by franchise
    # grouping. Persist exactly the cards written to the XLSX in this short
    # finalization transaction, after cancellation has been atomically ruled out.
    replace_task_results(db, task.id, companies)
    charge = spend_quota(sub, user, "organizations", len(companies))
    cost = len(companies)
    token = generate_token()
    dl = Download(
        user_id=user.id,
        export_task_id=None,
        filters_json=json.dumps({"kind": "parser", "subkind": "live_search", "source": task.source,
                                 "query": "; ".join(
                                     f"{job['query']} · {job['location']}".strip(" ·") for job in jobs
                                 )[:200]}),
        row_count=len(companies),
        credits_charged=cost,
        file_path=str(file_path),
        expires_at=utcnow() + timedelta(days=36500),
        org_ids_json=json.dumps([c.get("oid") for c in companies if c.get("oid")]),
    )
    db.add(dl)
    db.flush()

    saved_result_id = None
    try:
        saved = create_saved_result_from_rows(
            db,
            user_id=user.id,
            parser_task_id=task.id,
            title=None,
            params={**params, "source": task.source},
            rows=companies,
        )
        saved_result_id = saved.id
    except Exception:  # noqa: BLE001
        log.warning("Live Index save failed for parser_task=%s", task.id, exc_info=True)

    task.credits_charged = cost
    task.org_count = len(companies)
    task.download_id = dl.id
    task.download_token = token
    if saved_result_id:
        task.result_json = json.dumps({"saved_result_id": saved_result_id}, ensure_ascii=False)
    task.progress = 100
    if len(companies) < target_count:
        task.message = (
            f"Собрано {len(companies)} из {target_count}: доступная выдача источников исчерпана"
        )
    else:
        task.message = f"Собрано организаций: {len(companies)}"
    db.commit()


# ---------------------------------------------------------------------------
# enrich
# ---------------------------------------------------------------------------
async def _run_enrich(db, task: ParserTask, user: User) -> None:
    if stop_if_cancelled(db, task):
        return
    params = json.loads(task.params_json or "{}")
    oid = str(params.get("oid") or "").strip()
    if not oid:
        raise RuntimeError("Не указан org_id для обогащения")

    sub = get_or_create_subscription(db, user.id)
    if quota_available(sub, user, "enrichment") < 1:
        task.status = ExportStatus.failed
        task.error = "Месячный лимит обогащения исчерпан."
        db.commit()
        return

    task.progress = 30
    task.message = "Загружаем карточку организации…"
    db.commit()

    if stop_if_cancelled(db, task):
        return
    company = await asyncio.to_thread(yandex_search.enrich_one, oid)

    # Хотя бы какие-то данные должны быть, иначе не списываем
    has_data = any([
        company.get("name", "—") != "—",
        company.get("phone", "—") != "—",
        company.get("site", "—") != "—",
        company.get("social", "—") != "—",
        company.get("services"),
        company.get("rating", "—") != "—",
    ])
    if not has_data:
        task.status = ExportStatus.failed
        task.error = "Не удалось получить данные карточки (возможно, капча). Квота не списана."
        db.commit()
        return

    if stop_if_cancelled(db, task):
        return
    if not finalize_task_unless_cancelled(db, task, ExportStatus.done):
        return
    spend_quota(sub, user, "enrichment", 1)
    task.credits_charged = 1
    task.result_json = json.dumps(company, ensure_ascii=False)
    task.org_count = 1
    task.progress = 100
    task.message = "Готово"
    db.commit()


# ---------------------------------------------------------------------------
# org_zip (реализуется в итерации «Лендинг-ZIP»)
# ---------------------------------------------------------------------------
async def _run_org_zip(db, task: ParserTask, user: User) -> None:
    from app.services.maps import yandex_org  # noqa: PLC0415

    if stop_if_cancelled(db, task):
        return
    params = json.loads(task.params_json or "{}")
    oid = str(params.get("oid") or "").strip()
    if not oid:
        raise RuntimeError("Не указан org_id для сбора лендинга")

    sub = get_or_create_subscription(db, user.id)
    if quota_available(sub, user, "enrichment") < 1:
        task.status = ExportStatus.failed
        task.error = "Месячный лимит обогащения исчерпан."
        db.commit()
        return

    holder = {"message": "Запуск…"}
    lock = threading.Lock()

    def emit(event=None, **kw):
        with lock:
            if kw.get("message"):
                holder["message"] = kw["message"]

    os.makedirs(settings.export_dir, exist_ok=True)
    out_dir = Path(settings.export_dir)

    if stop_if_cancelled(db, task):
        return
    build_coro = asyncio.to_thread(yandex_org.collect_org_zip, oid, out_dir, emit)
    build_task = asyncio.ensure_future(build_coro)
    pct = 10
    while not build_task.done():
        await asyncio.sleep(2)
        if stop_if_cancelled(db, task):
            return
        with lock:
            msg = holder["message"]
        pct = min(95, pct + 5)
        task.progress = pct
        task.message = msg[:300] if msg else None
        db.commit()
    zip_path = await build_task

    if not zip_path or not os.path.exists(zip_path):
        task.status = ExportStatus.failed
        task.error = "Не удалось собрать данные организации. Квота не списана."
        db.commit()
        return

    if stop_if_cancelled(db, task):
        return
    if not finalize_task_unless_cancelled(db, task, ExportStatus.done):
        try:
            Path(zip_path).unlink(missing_ok=True)
        except OSError:
            pass
        return
    spend_quota(sub, user, "enrichment", 1)
    cost = 1
    token = generate_token()
    dl = Download(
        user_id=user.id,
        export_task_id=None,
        filters_json=json.dumps({"kind": "parser", "subkind": "org_zip", "oid": oid}),
        row_count=1,
        credits_charged=cost,
        file_path=str(zip_path),
        expires_at=utcnow() + timedelta(days=36500),
        org_ids_json=json.dumps([oid]),
    )
    db.add(dl)
    db.flush()
    task.credits_charged = cost
    task.org_count = 1
    task.download_id = dl.id
    task.download_token = token
    task.progress = 100
    task.message = "Архив готов"
    db.commit()


async def _run_company_analysis(db, task: ParserTask, user: User) -> None:
    """Пакетно собирает публичные данные карточек Яндекс без повторного анализа отзывов."""
    from app.services.maps import yandex_org  # noqa: PLC0415

    if stop_if_cancelled(db, task):
        return
    params = json.loads(task.params_json or "{}")
    organizations = params.get("organizations") or []
    if not organizations:
        raise RuntimeError("Не выбраны организации для анализа")

    sub = get_or_create_subscription(db, user.id)
    available = quota_available(sub, user, "enrichment")
    if available < len(organizations):
        task.status = ExportStatus.failed
        task.error = f"Недостаточно квоты анализа компаний: нужно {len(organizations)}, доступно {available}."
        db.commit()
        return

    os.makedirs(settings.export_dir, exist_ok=True)
    out_dir = Path(settings.export_dir)
    bundle_path = out_dir / f"company_analysis_{task.id}.zip"
    completed: list[tuple[dict, str]] = []
    errors: list[str] = []
    total = len(organizations)

    for index, organization in enumerate(organizations, 1):
        if stop_if_cancelled(db, task):
            return
        name = str(organization.get("name") or organization.get("oid") or "Организация")
        task.message = f"{name}: загружаем карточку, фото, услуги и новости"
        task.progress = min(95, int((index - 1) * 100 / total))
        db.commit()

        def emit(event=None, **kw):
            message = str(kw.get("message") or "").strip()
            if message:
                task.message = f"{name}: {message}"[:300]

        try:
            path = await asyncio.to_thread(
                yandex_org.collect_org_zip,
                str(organization.get("oid") or ""),
                out_dir,
                emit,
                0,
                20,
                False,
            )
            if path and os.path.exists(path):
                completed.append((organization, path))
            else:
                errors.append(f"{name}: источник не вернул данные")
        except Exception as exc:  # noqa: BLE001
            log.warning("company analysis failed for %s: %s", name, exc)
            errors.append(f"{name}: {type(exc).__name__}")
        task.progress = min(95, int(index * 100 / total))
        db.commit()

    if not completed:
        task.status = ExportStatus.failed
        task.error = "[COMPANY-204] Не удалось собрать ни одной компании. Квота не списана. " + "; ".join(errors[:3])
        db.commit()
        return

    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for index, (organization, path) in enumerate(completed, 1):
            name = str(organization.get("name") or organization.get("oid") or "company")
            safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:70]
            bundle.write(path, arcname=f"{index:02d}_{safe_name}.zip")
            try:
                os.remove(path)
            except OSError:
                pass
        if errors:
            bundle.writestr("errors.txt", "\n".join(errors))

    cost = len(completed)
    if stop_if_cancelled(db, task):
        return
    if not finalize_task_unless_cancelled(db, task, ExportStatus.done):
        try:
            bundle_path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    spend_quota(sub, user, "enrichment", cost)
    token = generate_token()
    dl = Download(
        user_id=user.id,
        export_task_id=None,
        filters_json=json.dumps({
            "kind": "parser",
            "subkind": "company_analysis",
            "saved_result_id": params.get("saved_result_id"),
        }),
        row_count=cost,
        credits_charged=cost,
        file_path=str(bundle_path),
        expires_at=utcnow() + timedelta(days=36500),
        org_ids_json=json.dumps([organization.get("oid") for organization, _ in completed]),
    )
    db.add(dl)
    db.flush()
    task.credits_charged = cost
    task.org_count = cost
    task.download_id = dl.id
    task.download_token = token
    task.progress = 100
    task.message = f"Анализ готов: {cost} из {total}"
    db.commit()


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------
async def _run_async(task_id: str) -> None:
    db = SessionLocal()
    try:
        task: Optional[ParserTask] = db.get(ParserTask, task_id)
        if not task:
            return
        if stop_if_cancelled(db, task):
            return
        user: Optional[User] = db.get(User, task.user_id)
        if not user:
            task.status = ExportStatus.failed
            task.error = "Пользователь не найден"
            task.finished_at = utcnow()
            db.commit()
            return

        task.status = ExportStatus.running
        task.started_at = task.started_at or utcnow()
        db.commit()

        try:
            if stop_if_cancelled(db, task):
                return
            if task.kind == "live_search":
                await _run_live_search(db, task, user)
            elif task.kind == "live_preview":
                await _run_preview(db, task, user)
            elif task.kind == "enrich":
                await _run_enrich(db, task, user)
            elif task.kind == "org_zip":
                await _run_org_zip(db, task, user)
            elif task.kind == "company_analysis":
                await _run_company_analysis(db, task, user)
            else:
                task.status = ExportStatus.failed
                task.error = f"Неизвестный тип задачи: {task.kind}"
                db.commit()
            if task.status in {ExportStatus.done, ExportStatus.failed, ExportStatus.partial, ExportStatus.canceled}:
                task.finished_at = task.finished_at or utcnow()
                db.commit()
        except Exception as exc:  # noqa: BLE001
            log.exception("parser_task %s failed", task_id)
            _record_parser_failure(db, task_id, exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("parser_task %s failed during startup", task_id)
        _record_parser_failure(db, task_id, exc)
    finally:
        current = db.get(ParserTask, task_id)
        if current and current.status in {
            ExportStatus.done, ExportStatus.failed, ExportStatus.partial, ExportStatus.canceled
        } and current.finished_at is None:
            current.finished_at = utcnow()
            db.commit()
        db.close()


def run_parser_task(task_id: str) -> None:
    """Точка входа для BackgroundTasks. Создаёт свой event loop."""
    try:
        asyncio.run(_run_async(task_id))
    except Exception:  # noqa: BLE001
        log.exception("run_parser_task wrapper failed for %s", task_id)
