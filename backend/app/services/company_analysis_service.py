from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import asyncio, ipaddress, json, logging, os, re, shutil, socket, uuid, zipfile
from collections.abc import Callable
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
import httpx
from sqlalchemy import select
from app.config import get_settings
from app.db import SessionLocal
from app.models import Business, BusinessSource, CompanyAnalysisTask, Download, ExportStatus, SavedResultItem, User
from app.security import generate_token
from app.services.admin_incident_service import record_incident, safe_text
from app.services.admin_task_service import finalize_task_unless_cancelled, stop_if_cancelled
from app.services.company_analysis_xlsx import write_company_analysis_xlsx
from app.services.llm_client import chat_json
from app.services.maps import google_search, yandex_org
from app.services.reviews_parser import extract_org_id
from app.services.subscription_service import get_or_create_subscription, quota_available, spend_quota
from app.timeutils import utcnow
from app.services.deep_analysis import Deduplicator, ManifestBuilder
from app.services.deep_analysis.website import scrape_website
from app.services.deep_analysis.vk import scrape_vk
from app.services.deep_analysis.telegram import scrape_telegram

log = logging.getLogger(__name__)
settings = get_settings()
_MODULE_KEYS = ("photos", "reviews", "services_prices", "website", "telegram", "vk")

def normalize_company_analysis_options(payload: dict) -> dict:
    options = {key: bool(payload.get(key, False)) for key in _MODULE_KEYS}
    if not any(options.values()): raise ValueError("Выберите хотя бы один модуль анализа")
    try:
        review_count = int(payload.get("review_count") or 50)
        rating_min = int(payload.get("review_rating_min") or 1)
        rating_max = int(payload.get("review_rating_max") or 5)
    except (TypeError, ValueError) as exc: raise ValueError("Некорректные настройки отзывов") from exc
    if options["reviews"] and not 1 <= review_count <= 500: raise ValueError("Количество отзывов должно быть от 1 до 500")
    if not (1 <= rating_min <= 5 and 1 <= rating_max <= 5 and rating_min <= rating_max): raise ValueError("Некорректный диапазон рейтинга отзывов")
    raw_eligible = payload.get("eligible_business_ids") if isinstance(payload, dict) else {}
    eligible_business_ids = {
        key: [int(value) for value in (raw_eligible.get(key) or []) if str(value).isdigit()]
        for key in ("website", "telegram", "vk")
    } if isinstance(raw_eligible, dict) else {key: [] for key in ("website", "telegram", "vk")}
    return {
        **options,
        "review_count": review_count,
        "review_rating_min": rating_min,
        "review_rating_max": rating_max,
        "eligible_business_ids": eligible_business_ids,
    }

def _has_useful_data(row: dict) -> bool:
    if row.get("_source_collected") is False: return False
    company = row.get("company") or {}
    if any(str(value or "").strip(" —-") for value in company.values()): return True
    return any(row.get(key) for key in ("reviews", "photos", "services", "prices", "news", "web", "telegram", "vk"))

async def collect_company_batch(targets, options, collector, on_progress=None):
    companies, successful, partial, failed = [], 0, 0, 0
    total = len(targets)
    for index, target in enumerate(targets, 1):
        events = []
        def emit(event=None, **payload):
            snapshot = {"event": event or "progress", **payload}
            events.append(snapshot)
            if on_progress: on_progress({"processed_count": index - 1, "target_count": total, "current_company": target.get("name") or "Организация", **snapshot})
        try:
            row = await collector(target, options, emit) if asyncio.iscoroutinefunction(collector) else await asyncio.to_thread(collector, target, options, emit)
        except Exception as exc:
            row = {"business_id": target.get("business_id"), "source": target.get("source"), "company": {}, "module_errors": {"base": f"{type(exc).__name__}: {str(exc)[:240]}"}}
        row = row if isinstance(row, dict) else {}
        row.setdefault("business_id", target.get("business_id"))
        row.setdefault("source", target.get("source"))
        row.setdefault("source_url", target.get("source_url"))
        for key, default in {"company": {}, "reviews": [], "photos": [], "services": [], "prices": [], "news": [], "web": {}, "telegram": {}, "vk": {}, "module_errors": {}}.items(): row.setdefault(key, default)
        useful = _has_useful_data(row)
        row.pop("_source_collected", None)
        if not useful: status, failed = "failed", failed + 1
        elif row.get("module_errors"): status, partial = "partial", partial + 1
        else: status, successful = "success", successful + 1
        row["status"], row["events"] = status, events[-12:]
        companies.append(row)
        if on_progress: on_progress({"processed_count": index, "target_count": total, "current_company": target.get("name") or "Организация", "successful_count": successful, "partial_count": partial, "failed_count": failed, "event": "company_complete"})
    return {"companies": companies, "successful_count": successful, "partial_count": partial, "failed_count": failed, "billable_count": successful + partial}

def new_company_analysis_task_id() -> str: return str(uuid.uuid4())

def _split_values(raw):
    if isinstance(raw, list): return [str(v).strip() for v in raw if str(v).strip()]
    if not raw: return []
    import re
    return [v.strip() for v in re.split(r'[,;\s]+', str(raw)) if v.strip()]


def _meaningful(value: object) -> bool:
    return bool(str(value or "").strip(" —-"))


def _merge_company_facts(saved: dict, collected: dict) -> dict:
    merged = dict(saved or {})
    for key, value in (collected or {}).items():
        if _meaningful(value):
            merged[key] = value
    return merged


def _normalize_price(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d+(?:[\s\u00a0]\d{3})*", text):
        digits = re.sub(r"\D", "", text)
        return f"{int(digits):,} ₽".replace(",", " ")
    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        return f"{int(float(text.replace(',', '.'))):,} ₽".replace(",", " ")
    return text.replace("руб.", "₽").replace("руб", "₽").strip()


def normalize_service_positions(raw_services: object, raw_prices: object = None) -> list[dict[str, str]]:
    values = raw_services if isinstance(raw_services, list) else re.split(r"[;\n]+", str(raw_services or ""))
    positions: list[dict[str, str]] = []
    for value in values:
        if isinstance(value, dict):
            name = str(value.get("name") or value.get("title") or "").strip()
            price = _normalize_price(value.get("price") or value.get("amount"))
        else:
            text = str(value or "").strip()
            match = re.match(r"^(.*?)(?:\s+[—–-]\s+)(.+)$", text)
            if match:
                name, price = match.group(1).strip(), _normalize_price(match.group(2))
            else:
                name, price = text, ""
        if name and _meaningful(name):
            position = {"name": name, "price": price}
            if position not in positions:
                positions.append(position)
    return positions

def _rating(review: dict) -> int:
    try: return int(float(str(review.get("rating") or review.get("stars") or 0).replace(",", ".")))
    except (TypeError, ValueError): return 0

def _collect_yandex(target, options, emit, work_dir):
    oid = str(target.get("source_id") or extract_org_id(target.get("source_url")) or "").strip()
    if not oid: return {"company": {}, "module_errors": {"base": "Не найден ID карточки Яндекс"}}
    target_dir = work_dir / str(target["business_id"]); target_dir.mkdir(parents=True, exist_ok=True)
    archive = yandex_org.collect_org_zip(oid, target_dir, emit, max_reviews=int(options.get("review_count") or 50) if options.get("reviews") else 0, max_photos=20 if options.get("photos") else 0, include_reviews=bool(options.get("reviews")))
    if not archive or not os.path.exists(archive): return {"company": {}, "module_errors": {"base": "Яндекс Карты не вернули данные"}}
    result = {"company": {}, "reviews": [], "photos": [], "services": [], "prices": [], "news": [], "module_errors": {}}
    try:
        with zipfile.ZipFile(archive) as source:
            names = set(source.namelist())
            if "info.json" in names: result["company"] = json.loads(source.read("info.json"))
            if options.get("reviews") and "reviews.json" in names:
                reviews = json.loads(source.read("reviews.json"))
                result["reviews"] = [r for r in reviews if int(options.get("review_rating_min") or 1) <= _rating(r) <= int(options.get("review_rating_max") or 5)][:int(options.get("review_count") or 50)]
            if "news.json" in names: result["news"] = json.loads(source.read("news.json"))
            if options.get("photos"):
                photo_dir = target_dir / "photos"; photo_dir.mkdir(exist_ok=True)
                for name in sorted(v for v in names if v.startswith("photos/") and not v.endswith("/")):
                    dest = photo_dir / Path(name).name; dest.write_bytes(source.read(name)); result["photos"].append(str(dest))
    except Exception as exc: result["module_errors"]["archive"] = f"Архив Яндекс: {str(exc)[:180]}"
    company = result.get("company") or {}
    if options.get("services_prices"):
        result["services"] = normalize_service_positions(
            company["service_positions"] if "service_positions" in company else company.get("services")
        )
    try: os.remove(archive)
    except OSError: pass
    return result

@lru_cache(maxsize=256)
def _is_public_host(hostname: str) -> bool:
    if not hostname or hostname.lower() in {"localhost", "localhost.localdomain"}: return False
    try: addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except OSError: return False
    for address in addresses:
        try:
            if not ipaddress.ip_address(address).is_global: return False
        except ValueError: return False
    return True

def _channel_url(target: dict, domain_markers: tuple) -> str:
    for value in _split_values(target.get("social")):
        if any(marker in value.lower() for marker in domain_markers): return value
    return ""

async def collect_company_target(target, options, emit, work_dir, dedup, manifest):
    source = str(target.get("source") or "").lower()
    result = {"business_id": target.get("business_id"), "source": source, "source_url": target.get("source_url"), "company": {"name": target.get("name"), "address": target.get("address"), "phone": target.get("phone"), "site": target.get("website"), "social": target.get("social")}, "reviews": [], "photos": [], "services": [], "prices": [], "news": [], "web": {}, "telegram": {}, "vk": {}, "module_errors": {}}
    if source == "google":
        base_result = await asyncio.to_thread(google_search.collect_company_analysis, str(target.get("source_url") or ""), options, emit)
        if isinstance(base_result, dict):
            for k in ["reviews", "photos", "services", "prices", "news", "module_errors"]:
                if k in base_result: result[k] = base_result[k]
            result["company"] = _merge_company_facts(result["company"], base_result.get("company"))
    else:
        base_result = await asyncio.to_thread(_collect_yandex, target, options, emit, work_dir)
        if isinstance(base_result, dict):
            for k in ["reviews", "photos", "services", "prices", "news", "module_errors"]:
                if k in base_result: result[k] = base_result[k]
            result["company"] = _merge_company_facts(result["company"], base_result.get("company"))
    result["services"] = normalize_service_positions(result.get("services"))
    result["prices"] = list(result.get("prices") or [])
    eligible_ids = options.get("eligible_business_ids") or {}
    business_id = int(target.get("business_id") or 0)
    channels = {
        "web": (options.get("website") and business_id in eligible_ids.get("website", []), target.get("website")),
        "telegram": (options.get("telegram") and business_id in eligible_ids.get("telegram", []), _channel_url(target, ("t.me", "telegram"))),
        "vk": (options.get("vk") and business_id in eligible_ids.get("vk", []), _channel_url(target, ("vk.com", "vkontakte"))),
    }
    target_deep_dir = work_dir / "deep" / str(target["business_id"])
    for key, (enabled, url) in channels.items():
        if not enabled: continue
        if not url: result["module_errors"][key] = "Ссылка не указана"; continue
        try:
            emit("progress", module=key, message=f"Анализируем {key}")
            if key == "web": result["web"] = await scrape_website(str(url), target_deep_dir, dedup, manifest, max_pages=5)
            elif key == "vk": result["vk"] = scrape_vk(url.rstrip("/").split("/")[-1], target_deep_dir, dedup, manifest, max_posts=30)
            elif key == "telegram": result["telegram"] = await scrape_telegram(url.rstrip("/").split("/")[-1].lstrip("@"), target_deep_dir, dedup, manifest, max_messages=30)
        except Exception as exc: result["module_errors"][key] = str(exc)[:220]
    result["_source_collected"] = _has_useful_data(result)
    return result

def _load_targets(db, task):
    ids = [int(v) for v in json.loads(task.business_ids_json or "[]")]
    items = db.execute(select(SavedResultItem, Business).join(Business, Business.id == SavedResultItem.business_id).where(SavedResultItem.saved_result_id == task.saved_result_id, SavedResultItem.business_id.in_(ids))).all()
    by_id = {}
    for item, business in items:
        source = str(item.source or "").lower()
        source_row = db.execute(select(BusinessSource).where(BusinessSource.business_id == business.id, BusinessSource.source == source).limit(1)).scalar_one_or_none()
        by_id[business.id] = {"business_id": business.id, "name": business.name, "address": business.address, "phone": business.phone, "website": business.website, "social": business.social, "source": source, "source_id": source_row.source_id if source_row else None, "source_url": source_row.source_url if source_row else None}
    return [by_id[v] for v in ids if v in by_id]

def _preview_result(result: dict) -> dict:
    preview = {k: v for k, v in result.items() if k != "companies"}
    preview["companies"] = [{**dict(row), "reviews": list(row.get("reviews") or [])[:50], "photos": list(row.get("photos") or [])[:20]} for row in result.get("companies") or []]
    for item in preview["companies"]: item.pop("events", None); item.pop("_source_collected", None)
    return preview

def _build_artifacts(task, result, work_dir, summary=None):
    options = normalize_company_analysis_options(json.loads(task.options_json or "{}"))
    base_dir = work_dir / "structured"
    if base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    for row in result.get("companies") or []:
        biz_id = row.get("business_id")
        company_dir = base_dir / "companies" / str(biz_id)
        company_dir.mkdir(parents=True, exist_ok=True)

        selected_payloads = []
        if options.get("reviews"):
            selected_payloads.append(("reviews.json", row.get("reviews") or []))
        if options.get("services_prices"):
            selected_payloads.append(("services_prices.json", {
                "services": row.get("services") or [],
                "prices": row.get("prices") or [],
            }))
        for filename, payload in selected_payloads:
            (company_dir / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        for key, option_key, filename in [("web", "website", "website.json"), ("vk", "vk", "vk.json"), ("telegram", "telegram", "telegram.json")]:
            if options.get(option_key) and row.get(key):
                fpath = company_dir / filename
                fbytes = json.dumps(row[key], ensure_ascii=False, indent=2).encode("utf-8")
                fpath.write_bytes(fbytes)

        if options.get("photos") and row.get("photos"):
            photos_dir = company_dir / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            for pstr in row.get("photos", []):
                p = Path(pstr)
                if p.exists() and p.is_file():
                    shutil.copy2(p, photos_dir / p.name)

    zip_path = Path(settings.export_dir) / f"company_analysis_{task.id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for fpath in base_dir.rglob("*"):
            if fpath.is_file():
                archive.write(fpath, fpath.relative_to(base_dir))

    xlsx_path = Path(settings.export_dir) / f"company_analysis_{task.id}.xlsx"
    if any(options.get(k) for k in ("reviews", "services_prices", "website", "telegram", "vk")):
        write_company_analysis_xlsx(xlsx_path, result, summary, options)
    else:
        xlsx_path = None

    return xlsx_path, zip_path

async def _run_company_analysis_async(task_id: str) -> None:
    db = SessionLocal()
    try:
        task = db.get(CompanyAnalysisTask, task_id)
        if not task or stop_if_cancelled(db, task): return
        user = db.get(User, task.user_id)
        if not user:
            task.status, task.error, task.finished_at = ExportStatus.failed, "[ANALYSIS-301] Пользователь не найден", utcnow(); db.commit(); return
        task.status, task.started_at, task.message = ExportStatus.running, utcnow(), "Подготавливаем глубокий анализ"; db.commit()
        if stop_if_cancelled(db, task): return
        targets = _load_targets(db, task); options = normalize_company_analysis_options(json.loads(task.options_json or "{}"))
        if not targets: raise RuntimeError("Не найдены выбранные организации")
        sub = get_or_create_subscription(db, user.id)
        if quota_available(sub, user, "enrichment") < len(targets):
            task.status, task.error = ExportStatus.failed, f"[ANALYSIS-401] Недостаточно операций: нужно {len(targets)}, доступно {quota_available(sub, user, 'enrichment')}"; db.commit(); return
        work_dir = Path(settings.export_dir) / f"company_analysis_{task.id}"; work_dir.mkdir(parents=True, exist_ok=True)
        dedup, manifest = Deduplicator(work_dir), ManifestBuilder()
        holder = {"processed_count": 0, "target_count": len(targets), "message": "Запуск"}
        def on_progress(snapshot: dict):
            holder.update(snapshot)
            if snapshot.get("message"): holder["message"] = snapshot["message"]
        async def collector(target, normalized_options, emit): return await collect_company_target(target, normalized_options, emit, work_dir, dedup, manifest)
        batch_task = asyncio.create_task(collect_company_batch(targets, options, collector, on_progress))
        while not batch_task.done():
            await asyncio.sleep(1)
            if stop_if_cancelled(db, task): return
            processed = int(holder.get("processed_count") or 0)
            task.progress, task.message, task.progress_json = min(88, int(processed * 88 / len(targets))), str(holder.get("message") or holder.get("current_company") or "Анализируем")[:300], json.dumps(holder, ensure_ascii=False); db.commit()
        result = await batch_task
        if result["billable_count"] <= 0:
            task.status, task.finished_at, task.error, task.result_json = ExportStatus.failed, utcnow(), "[ANALYSIS-204] Не удалось получить данные", json.dumps(_preview_result(result), ensure_ascii=False); db.commit(); return
        task.progress, task.message = 90, "Формируем структурированный архив и XLSX"; db.commit()
        if stop_if_cancelled(db, task): return
        xlsx_path, zip_path = await asyncio.to_thread(_build_artifacts, task, result, work_dir); charge = result["billable_count"]
        if stop_if_cancelled(db, task): return
        task.progress, task.message = 96, "Готовим AI-сводку"; db.commit()
        await _generate_summary(db, task, result, xlsx_path, zip_path)
        if stop_if_cancelled(db, task): return
        if not finalize_task_unless_cancelled(db, task, ExportStatus.done): return
        spend_quota(sub, user, "enrichment", charge)
        xlsx_token, zip_token = generate_token(), generate_token()
        common = {"user_id": user.id, "export_task_id": None, "filters_json": json.dumps({"kind": "company_analysis", "task_id": task.id}), "row_count": charge, "credits_charged": charge, "expires_at": utcnow() + timedelta(days=36500), "org_ids_json": task.business_ids_json}
        zip_download = Download(file_path=str(zip_path), **common); downloads = [zip_download]
        xlsx_download = Download(file_path=str(xlsx_path), **common) if xlsx_path else None
        if xlsx_download: downloads.insert(0, xlsx_download)
        db.add_all(downloads); db.flush()
        if xlsx_download: task.xlsx_download_id, task.xlsx_download_token = xlsx_download.id, xlsx_token
        task.zip_download_id, task.zip_download_token, task.quota_charged, task.org_count = zip_download.id, zip_token, charge, charge
        task.result_json, task.progress, task.finished_at, task.message = json.dumps(_preview_result(result), ensure_ascii=False), 100, utcnow(), f"Анализ готов: {charge} компаний"; db.commit()
    except Exception as exc:
        log.exception("company analysis task %s failed", task_id); db.rollback()
        task = db.get(CompanyAnalysisTask, task_id)
        if task:
            task.status, task.finished_at, task.error = ExportStatus.failed, utcnow(), f"[ANALYSIS-500] {type(exc).__name__}: {safe_text(str(exc))[:400]}"
            record_incident(db, code="ANALYSIS-500", source="company_analysis_service", message=f"{type(exc).__name__} while running company analysis task", severity="critical", user_id=task.user_id, task_type="company", task_id=task.id, details={"exception_type": type(exc).__name__})
    finally:
        current = db.get(CompanyAnalysisTask, task_id)
        if current and current.status in {ExportStatus.done, ExportStatus.failed, ExportStatus.partial, ExportStatus.canceled} and current.finished_at is None: current.finished_at = utcnow(); db.commit()
        db.close()

async def _generate_summary(db, task, result, xlsx_path, zip_path): await regenerate_company_analysis_summary(db, task, result)

async def regenerate_company_analysis_summary(db, task, result=None):
    result = result or json.loads(task.result_json or "{}")
    summary, error = await summarize_company_analysis(result)
    if summary is None: task.summary_json, task.summary_error = None, f"[ANALYSIS-AI-205] {error or 'LLM не вернул сводку'}"; db.commit(); return False
    task.summary_json, task.summary_error = json.dumps(summary, ensure_ascii=False), None
    await asyncio.to_thread(_build_artifacts, task, result, Path(settings.export_dir) / f"company_analysis_{task.id}", summary); db.commit(); return True

_ANALYSIS_SYSTEM = "Ты B2B-аналитик DataMaps. Проанализируй собранные публичные данные компаний. Верни строго JSON на русском языке с полями: overview, strengths, weaknesses, reputation, digital_presence, pricing, recommendations, outliers. Для overview, digital_presence, pricing, recommendations и outliers используй только предоставленные публичные данные Яндекс Карт, сайта, Telegram-канала и сообщества ВКонтакте. Поля strengths и weaknesses заполняй только по текстам отзывов: каждая запись должна быть темой, прямо подтверждённой отзывами. Не используй рейтинг, цены, описание компании, сайт или соцсети как доказательство этих тем. Если текстов отзывов нет или в них нет достаточных данных, верни пустой массив. Не выдумывай факты."

def build_analysis_prompt(result: dict) -> str:
    compact = []
    for row in (result.get("companies") or [])[:20]:
        compact.append({
            "company": {k: (row.get("company") or {}).get(k) for k in ("name", "category", "address", "rating", "reviews_count", "site")},
            "source": row.get("source"),
            "status": row.get("status"),
            "services": list(row.get("services") or [])[:25],
            "prices": list(row.get("prices") or [])[:25],
            "reviews": [{"rating": r.get("rating") or r.get("stars"), "text": str(r.get("text") or "")[:500]} for r in (row.get("reviews") or [])[:30]],
            "photo_count": len(row.get("photos") or []),
            "news": list(row.get("news") or [])[:10],
            "web": {
                "url": (row.get("web") or {}).get("url"),
                "pages": [{
                    "url": page.get("url"), "title": page.get("title"), "description": page.get("description"), "text": str(page.get("text") or "")[:800],
                } for page in ((row.get("web") or {}).get("pages") or [])[:5]],
            },
            "telegram": {
                "url": (row.get("telegram") or {}).get("url"), "title": (row.get("telegram") or {}).get("title"), "description": (row.get("telegram") or {}).get("description"),
                "posts": [{"date": post.get("date"), "text": str(post.get("text") or "")[:500], "media": list(post.get("media") or [])[:4]} for post in ((row.get("telegram") or {}).get("posts") or [])[:8]],
            },
            "vk": {
                "url": (row.get("vk") or {}).get("url"), "title": (row.get("vk") or {}).get("title"), "description": (row.get("vk") or {}).get("description"),
                "posts": [{"date": post.get("date"), "text": str(post.get("text") or "")[:500], "media": list(post.get("media") or [])[:4]} for post in ((row.get("vk") or {}).get("posts") or [])[:8]],
            },
            "module_errors": row.get("module_errors") or {}
        })
    prompt_text = "Данные глубокого анализа компаний:\n" + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    return prompt_text[:30000]

def normalize_company_analysis_summary(summary: dict, result: dict) -> dict:
    normalized = dict(summary or {})

    def summary_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("text", "title", "name", "description", "message", "content"):
                if key in value:
                    text = summary_text(value[key])
                    if text:
                        return text
            return ""
        return str(value).strip()

    for key in ("overview", "reputation", "digital_presence", "pricing"):
        normalized[key] = summary_text(normalized.get(key))
    for key in ("strengths", "weaknesses", "recommendations", "outliers"):
        values = normalized.get(key)
        normalized[key] = [summary_text(value) for value in values if summary_text(value)] if isinstance(values, list) else []

    has_review_text = any(
        str(review.get("text") or "").strip()
        for row in result.get("companies") or []
        for review in row.get("reviews") or []
        if isinstance(review, dict)
    )
    for key, label in (("strengths", "сильных сторонах"), ("weaknesses", "слабых сторонах")):
        if not normalized[key] and not has_review_text:
            normalized[key] = [f"Недостаточно отзывов для вывода о {label}."]
    return normalized


async def summarize_company_analysis(result: dict):
    summary, error = await chat_json(
        _ANALYSIS_SYSTEM,
        build_analysis_prompt(result),
        temperature=0.2,
        max_tokens=1400,
        x_title="DataMaps Company Analysis",
    )
    return (normalize_company_analysis_summary(summary, result), None) if summary is not None else (None, error)

def run_company_analysis_task(task_id: str) -> None:
    try: asyncio.run(_run_company_analysis_async(task_id))
    except Exception: log.exception("company analysis wrapper failed for %s", task_id)
