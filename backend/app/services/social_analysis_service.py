from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import asyncio
import json
import logging
import re
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    Business,
    Download,
    ExportStatus,
    SocialAnalysisTask,
    SocialProfileSnapshot,
    User,
)
from app.security import generate_token
from app.services.admin_incident_service import record_incident, safe_text
from app.services.admin_task_service import finalize_task_unless_cancelled, stop_if_cancelled
from app.services.llm_client import chat_json
from app.services.subscription_service import get_or_create_subscription, quota_available, spend_quota
from app.timeutils import utcnow


log = logging.getLogger(__name__)
settings = get_settings()
_URL_RE = re.compile(r"https?://[^\s,;]+", re.I)
_SOCIAL_HOSTS = {
    "vk": ("vk.com", "vkontakte.ru"),
    "telegram": ("t.me", "telegram.me"),
    "instagram": ("instagram.com",),
}


def new_social_task_id() -> str:
    return str(uuid.uuid4())


def extract_social_urls(raw: Optional[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _URL_RE.findall(raw or ""):
        url = match.rstrip(".)]}")
        host = urlparse(url).netloc.lower().removeprefix("www.")
        platform = next(
            (name for name, domains in _SOCIAL_HOSTS.items() if any(host == d or host.endswith("." + d) for d in domains)),
            None,
        )
        if platform and url not in seen:
            seen.add(url)
            result.append((platform, url))
    return result


def _number(text: str, patterns: list[str]) -> Optional[int]:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        raw = match.group(1).replace(" ", "").replace("\xa0", "").replace(",", ".")
        multiplier = 1
        if raw.lower().endswith(("k", "к")):
            multiplier, raw = 1000, raw[:-1]
        elif raw.lower().endswith(("m", "м")):
            multiplier, raw = 1_000_000, raw[:-1]
        try:
            return int(float(raw) * multiplier)
        except ValueError:
            continue
    return None


def fetch_public_profile(platform: str, url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }
    try:
        with httpx.Client(timeout=18, follow_redirects=True, headers=headers) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return {"platform": platform, "url": url, "ok": False, "error": f"Ошибка соединения: {exc}"}
    if response.status_code >= 400:
        return {
            "platform": platform,
            "url": url,
            "ok": False,
            "error": f"Публичная страница вернула HTTP {response.status_code}",
        }
    soup = BeautifulSoup(response.text, "html.parser")
    title = ""
    description = ""
    for attr, target in (("og:title", "title"), ("og:description", "description")):
        tag = soup.find("meta", property=attr)
        value = (tag.get("content") if tag else "") or ""
        if target == "title":
            title = value.strip()
        else:
            description = value.strip()
    title = title or (soup.title.string.strip() if soup.title and soup.title.string else "")
    visible = soup.get_text(" ", strip=True)
    combined = f"{title} {description} {visible[:20000]}"
    followers = _number(combined, [
        r"([\d\s.,]+[kкmм]?)\s*(?:followers|подписчик)",
        r"(?:followers|подписчик)[^\d]{0,20}([\d\s.,]+[kкmм]?)",
        r"([\d\s.,]+[kкmм]?)\s*(?:members|участник)",
    ])
    posts = _number(combined, [r"([\d\s.,]+[kкmм]?)\s*(?:posts|публикаци|запис)" ])
    return {
        "platform": platform,
        "url": str(response.url),
        "ok": bool(title or description),
        "title": title[:300],
        "description": description[:1000],
        "followers": followers,
        "posts": posts,
        "fetched_at": utcnow().isoformat(),
        "error": None if title or description else "Страница доступна, но публичные метаданные не найдены",
    }


async def _summarize_social(name: str, profiles: list[dict]) -> tuple[dict, Optional[str]]:
    successful = [p for p in profiles if p.get("ok")]
    if not successful:
        return {}, None
    system = (
        "Ты аналитик публичных соцсетей бизнеса. Верни только JSON: summary (строка), "
        "activity (active|irregular|inactive|unknown), strengths (массив), weaknesses (массив), "
        "recommendations (массив до 5). Не выдумывай отсутствующие метрики."
    )
    payload = json.dumps({"company": name, "profiles": successful}, ensure_ascii=False)[:10000]
    parsed, error = await chat_json(system, payload, max_tokens=600, fast=True, x_title="DataMaps Social")
    return parsed or {}, error


def _write_xlsx(path: Path, rows: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Соцсети"
    headers = [
        "Организация", "Платформы", "Ссылки", "Подписчики", "Публикации",
        "Активность", "Сводка", "Сильные стороны", "Слабые стороны", "Рекомендации", "Ошибки",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for row in rows:
        profiles = row.get("profiles") or []
        summary = row.get("summary") or {}
        ws.append([
            row.get("name"),
            ", ".join(p.get("platform", "") for p in profiles),
            "\n".join(p.get("url", "") for p in profiles),
            ", ".join(str(p.get("followers")) for p in profiles if p.get("followers") is not None),
            ", ".join(str(p.get("posts")) for p in profiles if p.get("posts") is not None),
            summary.get("activity", ""),
            summary.get("summary", ""),
            "; ".join(summary.get("strengths") or []),
            "; ".join(summary.get("weaknesses") or []),
            "; ".join(summary.get("recommendations") or []),
            "; ".join(p.get("error", "") for p in profiles if p.get("error")),
        ])
    widths = [30, 18, 48, 16, 16, 16, 48, 40, 40, 48, 48]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, idx).column_letter].width = width
    wb.save(path)


async def _run(task_id: str) -> None:
    db = SessionLocal()
    try:
        task = db.get(SocialAnalysisTask, task_id)
        if not task:
            return
        if stop_if_cancelled(db, task):
            return
        user = db.get(User, task.user_id)
        if not user:
            task.status = ExportStatus.failed
            task.error = "Пользователь не найден"
            task.finished_at = utcnow()
            db.commit()
            return
        task.status = ExportStatus.running
        task.started_at = task.started_at or utcnow()
        db.commit()
        business_ids = [int(x) for x in json.loads(task.business_ids_json or "[]")]
        businesses = db.execute(select(Business).where(Business.id.in_(business_ids))).scalars().all()
        by_id = {b.id: b for b in businesses}
        results: list[dict] = []
        successful_orgs = 0
        for index, business_id in enumerate(business_ids, 1):
            if stop_if_cancelled(db, task):
                return
            business = by_id.get(business_id)
            profiles: list[dict] = []
            if business:
                for platform, url in extract_social_urls(business.social):
                    if stop_if_cancelled(db, task):
                        return
                    profile = await asyncio.to_thread(fetch_public_profile, platform, url)
                    profiles.append(profile)
                    if profile.get("ok"):
                        db.add(SocialProfileSnapshot(
                            business_id=business.id,
                            platform=platform,
                            url=url,
                            data_json=json.dumps(profile, ensure_ascii=False),
                            fetched_at=utcnow(),
                        ))
            if stop_if_cancelled(db, task):
                return
            summary, summary_error = await _summarize_social(business.name if business else "", profiles)
            ok = any(p.get("ok") for p in profiles)
            successful_orgs += int(ok)
            results.append({
                "business_id": business_id,
                "name": business.name if business else f"#{business_id}",
                "profiles": profiles,
                "summary": summary,
                "summary_error": summary_error,
                "ok": ok,
            })
            task.progress = min(99, int(index * 100 / max(1, len(business_ids))))
            task.message = f"Обработано {index} из {len(business_ids)}"
            db.commit()
        if not successful_orgs:
            task.status = ExportStatus.failed
            task.error = "[SOC-204] Не удалось получить публичные данные ни по одной социальной сети. Квота не списана."
            db.commit()
            return
        sub = get_or_create_subscription(db, user.id)
        available = quota_available(sub, user, "social")
        if available < successful_orgs:
            task.status = ExportStatus.failed
            task.error = f"[SOC-402] Нужно {successful_orgs} операций анализа соцсетей, доступно {available}."
            db.commit()
            return
        if stop_if_cancelled(db, task):
            return
        Path(settings.export_dir).mkdir(parents=True, exist_ok=True)
        path = Path(settings.export_dir) / f"social_{task.id}.xlsx"
        await asyncio.to_thread(_write_xlsx, path, results)
        if stop_if_cancelled(db, task):
            return
        if not finalize_task_unless_cancelled(db, task, ExportStatus.done):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        spend_quota(sub, user, "social", successful_orgs)
        token = generate_token()
        download = Download(
            user_id=user.id,
            export_task_id=None,
            filters_json=json.dumps({"kind": "social", "task_id": task.id}),
            row_count=successful_orgs,
            credits_charged=successful_orgs,
            file_path=str(path),
            expires_at=utcnow() + timedelta(days=36500),
            org_ids_json=json.dumps(business_ids),
        )
        db.add(download)
        db.flush()
        task.result_json = json.dumps(results, ensure_ascii=False)
        task.org_count = successful_orgs
        task.quota_charged = successful_orgs
        task.download_id = download.id
        task.download_token = token
        task.progress = 100
        task.finished_at = utcnow()
        task.message = f"Проанализировано организаций: {successful_orgs}"
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("social task %s failed", task_id)
        db.rollback()
        task = db.get(SocialAnalysisTask, task_id)
        if task:
            task.status = ExportStatus.failed
            task.finished_at = utcnow()
            task.error = f"[SOCIAL-500] {type(exc).__name__}: {safe_text(str(exc))[:400]}"
            record_incident(
                db,
                code="SOCIAL-500",
                source="social_analysis_service",
                message=f"{type(exc).__name__} while running social analysis task",
                severity="critical",
                user_id=task.user_id,
                task_type="social",
                task_id=task.id,
                details={"exception_type": type(exc).__name__},
            )
    finally:
        current = db.get(SocialAnalysisTask, task_id)
        if current and current.status in {
            ExportStatus.done, ExportStatus.failed, ExportStatus.partial, ExportStatus.canceled
        } and current.finished_at is None:
            current.finished_at = utcnow()
            db.commit()
        db.close()


def run_social_task(task_id: str) -> None:
    asyncio.run(_run(task_id))
