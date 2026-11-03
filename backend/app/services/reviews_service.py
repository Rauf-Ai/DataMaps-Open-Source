from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Оркестратор анализа отзывов: парсинг Яндекс Карт + AI-сводки + XLSX.

Запускается из BackgroundTasks через run_review_task(task_id).
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import timedelta
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal, get_duck
from app.models import Download, ExportStatus, ReviewTask, User
from app.security import generate_token
from app.services.admin_incident_service import record_incident, safe_text
from app.services.admin_task_service import finalize_task_unless_cancelled, stop_if_cancelled
from app.services.llm_client import summarize
from app.services.reviews_parser import (
    extract_org_id,
    fetch_reviews,
    throttle_between_orgs,
    throttle_between_orgs_deep,
)
from app.services.reviews_parser_browser import fetch_reviews_deep
from app.services.reviews_xlsx import write_reviews_xlsx
from app.services.subscription_service import get_or_create_subscription, quota_available, spend_quota
from app.timeutils import utcnow


log = logging.getLogger(__name__)
settings = get_settings()


def new_task_id() -> str:
    return str(uuid.uuid4())


def _record_review_failure(db, task_id: str, exc: Exception) -> None:
    db.rollback()
    task = db.get(ReviewTask, task_id)
    if not task:
        return
    task.status = ExportStatus.failed
    task.finished_at = utcnow()
    task.error = f"[REVIEWS-500] {type(exc).__name__}: {safe_text(str(exc))[:400]}"
    record_incident(
        db,
        code="REVIEWS-500",
        source="reviews_service",
        message=f"{type(exc).__name__} while running review task",
        severity="critical",
        user_id=task.user_id,
        task_type="review",
        task_id=task.id,
        details={"exception_type": type(exc).__name__},
    )


def _load_orgs(org_ids: list[int]) -> dict[int, dict]:
    """Из DuckDB загружает meta по списку ID. Для отсутствующих ID создаёт
    заглушку с URL Яндекс Карт — это позволяет парсить отзывы по произвольным
    ссылкам, даже если организации нет в нашей базе (имя/адрес возьмутся из
    JSON-LD страницы Яндекса самим парсером)."""
    if not org_ids:
        return {}
    placeholders = ",".join(["?"] * len(org_ids))
    sql = (
        f"SELECT id, name, COALESCE(address, ''), COALESCE(rating, 0), "
        f"COALESCE(reviews_count, 0), COALESCE(yandex_card_url, '') "
        f"FROM organizations WHERE id IN ({placeholders})"
    )
    out: dict[int, dict] = {}
    try:
        with get_duck() as cur:
            for r in cur.execute(sql, org_ids).fetchall():
                out[int(r[0])] = {
                    "id": int(r[0]),
                    "name": r[1] or "",
                    "address": r[2] or "",
                    "rating": float(r[3]) if r[3] else 0.0,
                    "reviews_count": int(r[4]) if r[4] else 0,
                    "yandex_card_url": r[5] or f"https://yandex.ru/maps/org/{int(r[0])}/",
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("DuckDB load_orgs failed: %s — продолжаем с заглушками", exc)
    # Для не найденных в базе — заглушка, по которой парсер всё равно работает
    for oid in org_ids:
        if oid not in out:
            out[oid] = {
                "id": int(oid),
                "name": "",
                "address": "",
                "rating": 0.0,
                "reviews_count": 0,
                "yandex_card_url": f"https://yandex.ru/maps/org/{int(oid)}/",
            }
    return out


async def _process_one(meta: dict, stars: tuple[int, int], deep: bool) -> dict:
    """Парсит отзывы одной организации и зовёт LLM. Возвращает результат для XLSX."""
    org_id_str = extract_org_id(meta.get("yandex_card_url"))
    result = {
        "org_id": meta["id"],
        "name": meta["name"],
        "address": meta["address"],
        "rating": meta["rating"],
        "reviews_count": meta["reviews_count"],
        "yandex_org_id": org_id_str,
        "reviews": [],
        "summary": None,
        "error": None,
    }
    if not org_id_str:
        result["error"] = "Не удалось извлечь Yandex org_id из ссылки"
        return result

    if deep:
        parsed = await fetch_reviews_deep(org_id_str, stars_min=stars[0], stars_max=stars[1])
    else:
        parsed = await asyncio.to_thread(fetch_reviews, org_id_str, stars_min=stars[0], stars_max=stars[1])
    if "error" in parsed:
        result["error"] = parsed["error"]
        return result

    info = parsed.get("company_info") or {}
    reviews = parsed.get("company_reviews") or []
    result["reviews"] = reviews
    if info.get("name"):
        result["name"] = info["name"]
    if info.get("rating"):
        result["rating"] = info["rating"]
    if info.get("count_rating"):
        result["reviews_count"] = info["count_rating"]

    # AI-сводка имеет смысл от 3 отзывов
    if len(reviews) >= 3:
        summary, summary_err = await summarize(result["name"], result["address"], result["rating"], reviews)
        result["summary"] = summary
        if summary_err:
            result["summary_error"] = summary_err
    return result


async def _run_review_task_async(task_id: str) -> None:
    db = SessionLocal()
    try:
        task: Optional[ReviewTask] = db.get(ReviewTask, task_id)
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
            org_ids: list[int] = json.loads(task.org_ids_json or "[]")
            stars = (task.star_min, task.star_max)
            deep = bool(task.deep)
            metas = _load_orgs(org_ids)
            if not metas:
                raise RuntimeError("Не нашлось ни одной организации по выбранным ID")

            results: list[dict] = []
            total = len(org_ids)
            throttle = throttle_between_orgs_deep if deep else throttle_between_orgs
            for i, oid in enumerate(org_ids):
                if stop_if_cancelled(db, task):
                    return
                meta = metas.get(oid)
                if not meta:
                    results.append({"org_id": oid, "name": f"#{oid}", "address": "",
                                    "rating": 0, "reviews_count": 0, "reviews": [], "summary": None,
                                    "error": "Не найдено в базе"})
                else:
                    res = await _process_one(meta, stars, deep)
                    results.append(res)
                # Прогресс
                task.progress = min(99, int((i + 1) * 100 / total))
                db.commit()
                if i < total - 1:
                    if stop_if_cancelled(db, task):
                        return
                    await asyncio.to_thread(throttle)

            # Считаем сумму к списанию ТОЛЬКО по успешно спарсенным организациям
            successful = [r for r in results if r.get("reviews")]
            total_reviews = sum(len(r["reviews"]) for r in successful)

            if not successful:
                errors = [r.get("error") for r in results if r.get("error")]
                err_sample = errors[0] if errors else "пустой ответ от Яндекс Карт (возможно, капча)"
                task.status = ExportStatus.failed
                task.error = (
                    "Не удалось получить отзывы ни по одной организации. "
                    f"Квота не списана. Причина: {err_sample}"
                )
                db.commit()
                return

            sub = get_or_create_subscription(db, user.id)
            cost = len(successful)
            available = quota_available(sub, user, "reviews")
            if available < cost:
                task.status = ExportStatus.failed
                task.error = (
                    f"Недостаточно квоты анализа отзывов: нужно {cost}, доступно {available}. "
                    "Парсинг выполнен, но файл не сохранён."
                )
                db.commit()
                return

            # XLSX
            if stop_if_cancelled(db, task):
                return
            os.makedirs(settings.export_dir, exist_ok=True)
            file_path = Path(settings.export_dir) / f"reviews_{task.id}.xlsx"
            await asyncio.to_thread(write_reviews_xlsx, file_path, results)

            # Списываем и фиксируем Download
            if stop_if_cancelled(db, task):
                return
            if not finalize_task_unless_cancelled(db, task, ExportStatus.done):
                try:
                    file_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return
            spend_quota(sub, user, "reviews", cost)
            token = generate_token()
            dl = Download(
                user_id=user.id,
                export_task_id=None,
                filters_json=json.dumps({"kind": "reviews", "task_id": task.id, "org_ids": org_ids}),
                row_count=total_reviews,
                credits_charged=cost,
                file_path=str(file_path),
                expires_at=utcnow() + timedelta(days=36500),
                org_ids_json=json.dumps(org_ids),
            )
            db.add(dl)
            db.flush()
            task.credits_charged = cost
            task.download_id = dl.id
            task.download_token = token
            task.progress = 100
            task.finished_at = utcnow()
            db.commit()
        except Exception as exc:  # noqa: BLE001
            log.exception("review_task %s failed", task_id)
            _record_review_failure(db, task_id, exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("review_task %s failed during startup", task_id)
        _record_review_failure(db, task_id, exc)
    finally:
        current = db.get(ReviewTask, task_id)
        if current and current.status in {
            ExportStatus.done, ExportStatus.failed, ExportStatus.partial, ExportStatus.canceled
        } and current.finished_at is None:
            current.finished_at = utcnow()
            db.commit()
        db.close()


def run_review_task(task_id: str) -> None:
    """Точка входа для BackgroundTasks. Создаёт свой event loop."""
    try:
        asyncio.run(_run_review_task_async(task_id))
    except Exception:  # noqa: BLE001
        log.exception("run_review_task wrapper failed for %s", task_id)
