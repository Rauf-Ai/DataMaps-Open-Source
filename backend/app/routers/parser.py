from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

"""Эндпоинты живого парсинга карт: поиск организаций, обогащение карточки,
сбор лендинг-ZIP, статус задач и скачивание результатов."""

import json
import os
from pathlib import Path

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
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    Business,
    BusinessSource,
    ChatMessage,
    ChatSession,
    CompanyAnalysisTask,
    Download,
    ExportStatus,
    ParserTask,
    SavedResult,
    SavedResultItem,
    User,
)
from app.services.maps_service import new_task_id, run_parser_task
from app.services.parser_task_results import read_task_results
from app.services.company_analysis_service import regenerate_company_analysis_summary
from app.services.subscription_service import ensure_task_slot, get_or_create_subscription, quota_available
from app.services.reviews_parser import extract_org_id
from app.timeutils import utcnow

settings = get_settings()
router = APIRouter(prefix="/api/parser", tags=["parser"])


def _require_verified(user: User) -> None:
    if user.email_verified_at is None:
        raise HTTPException(status_code=403, detail="Подтвердите email перед использованием парсера")


def _require_task_slot(db: Session, user: User):
    sub = get_or_create_subscription(db, user.id)
    try:
        ensure_task_slot(db, sub, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sub


def _parse_search_payload(payload: dict) -> tuple[str, int, dict]:
    """Валидирует общий payload живого поиска. Возвращает (source, max_per, params)."""
    raw = payload.get("searches")
    if not isinstance(raw, list) or not raw:
        raw = [{"query": payload.get("query"), "location": payload.get("location") or payload.get("city")}]
    searches = []
    for s in raw:
        q = (str(s.get("query") or "")).strip()
        loc = (str(s.get("location") or "")).strip()
        if not q:
            raise HTTPException(status_code=400, detail="Введите поисковый запрос в каждой строке")
        if not loc:
            raise HTTPException(status_code=400, detail="Укажите локацию (страна/регион/город) в каждой строке")
        row_source = str(s.get("source") or payload.get("source") or "yandex").lower()
        if row_source not in ("yandex", "google"):
            raise HTTPException(status_code=400, detail="Некорректный источник в строке поиска")
        searches.append({
            "query": q,
            "location": loc,
            "country": str(s.get("country") or "").strip(),
            "source": row_source,
        })
    if len(searches) > 50:
        raise HTTPException(status_code=400, detail="Слишком много строк поиска (максимум 50)")

    source = payload.get("source") or "yandex"
    if source not in ("yandex", "google"):
        raise HTTPException(status_code=400, detail="Некорректный источник")
    max_per = int(payload.get("target_count") or payload.get("max") or 50)
    if not (1 <= max_per <= 5000):
        raise HTTPException(status_code=400, detail="Количество организаций: от 1 до 5000")

    def _tri(key: str) -> str:
        v = str(payload.get(key) or "any").lower()
        return v if v in ("any", "yes", "no") else "any"

    social_nets = payload.get("social_nets")
    social_nets = [str(x) for x in social_nets] if isinstance(social_nets, list) else []

    params = {
        "searches": searches,
        "max": max_per,
        "target_count": max_per,
        "f_phone": _tri("f_phone"),
        "f_site": _tri("f_site"),
        "f_franchise": _tri("f_franchise"),
        "social_nets": social_nets,
        "reviews_min": int(payload.get("reviews_min") or 0),
        "reviews_max": int(payload["reviews_max"]) if payload.get("reviews_max") not in (None, "", 0) else None,
        "rating_min": float(payload.get("rating_min") or 0),
        "rating_max": float(payload.get("rating_max")) if payload.get("rating_max") not in (None, "") else 5.0,
    }
    return source, max_per, params


@router.post("/preview")
async def create_preview_task(
    payload: dict,
    background: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Бесплатный предпросмотр: собирает 1–10 организаций (пропорционально max)
    по первой строке, чтобы клиент увидел данные до списания кредитов."""
    _require_verified(user)
    _require_task_slot(db, user)
    source, _max_per, params = _parse_search_payload(payload)
    task = ParserTask(
        id=new_task_id(), user_id=user.id, status=ExportStatus.queued, progress=0,
        kind="live_preview", source=source, params_json=json.dumps(params, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    background.add_task(run_parser_task, task.id)
    return {"task_id": task.id}


@router.post("/search")
async def create_search_task(
    payload: dict,
    background: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_verified(user)
    source, max_per, params = _parse_search_payload(payload)

    # Мягкая проверка баланса: точная сумма — по факту собранных.
    sub = _require_task_slot(db, user)
    available = quota_available(sub, user, "organizations")
    if available <= 0:
        raise HTTPException(status_code=402, detail="Месячный лимит организаций исчерпан")

    task = ParserTask(
        id=new_task_id(), user_id=user.id, status=ExportStatus.queued, progress=0,
        kind="live_search", source=source, params_json=json.dumps(params, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    background.add_task(run_parser_task, task.id)
    return {"task_id": task.id, "available_organizations": available}


@router.post("/enrich")
async def create_enrich_task(
    payload: dict,
    background: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_verified(user)
    url = (payload.get("url") or "").strip()
    oid = extract_org_id(url)
    if not oid:
        raise HTTPException(
            status_code=400,
            detail="Не удалось распознать ссылку Яндекс Карт. Формат: https://yandex.ru/maps/org/<slug>/<id>/",
        )
    sub = _require_task_slot(db, user)
    if quota_available(sub, user, "enrichment") < 1:
        raise HTTPException(status_code=402, detail="Месячный лимит обогащения исчерпан")
    task = ParserTask(
        id=new_task_id(),
        user_id=user.id,
        status=ExportStatus.queued,
        progress=0,
        kind="enrich",
        source="yandex",
        params_json=json.dumps({"oid": oid, "url": url}, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    background.add_task(run_parser_task, task.id)
    return {"task_id": task.id}


@router.post("/org-zip")
async def create_org_zip_task(
    payload: dict,
    background: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_verified(user)
    url = (payload.get("url") or "").strip()
    oid = extract_org_id(url)
    if not oid:
        raise HTTPException(
            status_code=400,
            detail="Не удалось распознать ссылку Яндекс Карт. Формат: https://yandex.ru/maps/org/<slug>/<id>/",
        )
    sub = _require_task_slot(db, user)
    if quota_available(sub, user, "enrichment") < 1:
        raise HTTPException(status_code=402, detail="Месячный лимит обогащения исчерпан")
    task = ParserTask(
        id=new_task_id(),
        user_id=user.id,
        status=ExportStatus.queued,
        progress=0,
        kind="org_zip",
        source="yandex",
        params_json=json.dumps({"oid": oid, "url": url}, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    background.add_task(run_parser_task, task.id)
    return {"task_id": task.id}


def _company_analysis_targets(
    db: Session,
    user_id: int,
    saved_result_id: int,
    business_ids: list[int],
) -> list[dict]:
    saved = db.get(SavedResult, saved_result_id)
    if not saved or saved.user_id != user_id:
        raise HTTPException(status_code=404, detail="Сохранённый результат не найден")

    selected = list(dict.fromkeys(int(value) for value in business_ids))
    if not selected:
        raise HTTPException(status_code=400, detail="Не выбрано ни одной организации")
    if len(selected) > 20:
        raise HTTPException(status_code=400, detail="За раз можно анализировать максимум 20 организаций")

    allowed = set(db.execute(
        select(SavedResultItem.business_id).where(
            SavedResultItem.saved_result_id == saved_result_id,
            SavedResultItem.business_id.in_(selected),
        )
    ).scalars().all())
    if allowed != set(selected):
        raise HTTPException(status_code=400, detail="Выбраны организации не из этого результата")

    rows = db.execute(
        select(Business, BusinessSource)
        .join(BusinessSource, BusinessSource.business_id == Business.id)
        .where(
            Business.id.in_(selected),
            BusinessSource.source == "yandex",
        )
    ).all()
    by_business: dict[int, dict] = {}
    for business, source in rows:
        by_business.setdefault(business.id, {
            "business_id": business.id,
            "name": business.name,
            "oid": source.source_id,
            "source_url": source.source_url,
        })
    missing = [value for value in selected if value not in by_business]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "Анализ компании пока доступен только для организаций из Яндекс Карт. "
                f"Без карточки Яндекс: {len(missing)}"
            ),
        )
    return [by_business[value] for value in selected]


@router.post("/company-analysis")
async def create_company_analysis_task(
    payload: dict,
    background: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Собирает фото, товары/услуги, новости, особенности и цены выбранных компаний."""
    _require_verified(user)
    saved_result_id = int(payload.get("saved_result_id") or 0)
    raw_business_ids = payload.get("business_ids") or []
    if not isinstance(raw_business_ids, list):
        raise HTTPException(status_code=400, detail="Некорректный список организаций")
    try:
        targets = _company_analysis_targets(
            db, user.id, saved_result_id, [int(value) for value in raw_business_ids]
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректные ID организаций") from exc

    sub = _require_task_slot(db, user)
    available = quota_available(sub, user, "enrichment")
    if available < len(targets):
        raise HTTPException(
            status_code=402,
            detail=f"Недостаточно квоты анализа компаний: нужно {len(targets)}, доступно {available}",
        )

    task = ParserTask(
        id=new_task_id(),
        user_id=user.id,
        status=ExportStatus.queued,
        progress=0,
        kind="company_analysis",
        source="yandex",
        params_json=json.dumps({
            "saved_result_id": saved_result_id,
            "organizations": targets,
        }, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    background.add_task(run_parser_task, task.id)
    return {"task_id": task.id, "org_count": len(targets), "quota_required": len(targets)}


@router.get("/tasks/{task_id}/status")
async def parser_task_status(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    task = db.get(ParserTask, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    resp: dict = {
        "status": task.status.value,
        "progress": task.progress,
        "error": task.error,
        "message": task.message,
        "kind": task.kind,
        "org_count": task.org_count,
        "credits_charged": task.credits_charged,
    }
    if task.progress_json:
        try:
            progress = json.loads(task.progress_json)
            if isinstance(progress, dict):
                resp.update(progress)
        except json.JSONDecodeError:
            pass
    if task.status == ExportStatus.done:
        if task.download_token:
            resp["download_url"] = f"/api/parser/download?token={task.download_token}"
        if task.result_json:
            try:
                resp["result"] = json.loads(task.result_json)
            except json.JSONDecodeError:
                resp["result"] = None
    return resp


@router.get("/tasks/{task_id}/results")
async def parser_task_results(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    after: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=200),
):
    """Return only task cards that the client has not appended yet."""
    task = db.get(ParserTask, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    items, next_after = read_task_results(db, task.id, after=after, limit=limit)
    return {"items": items, "next_after": next_after}


def _json_value(raw: Optional[str], default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _ensure_analysis_completion_message(db: Session, task: CompanyAnalysisTask) -> None:
    if not task.chat_session_id or task.completion_notified or task.status not in {ExportStatus.done, ExportStatus.failed}:
        return
    session = db.get(ChatSession, task.chat_session_id)
    if not session:
        return
    details = _json_value(task.result_json, {})
    elapsed = 0
    if task.started_at and task.finished_at:
        elapsed = max(0, int((task.finished_at - task.started_at).total_seconds()))
    minutes, seconds = divmod(elapsed, 60)
    duration = f"{minutes} мин {seconds} сек" if minutes else f"{seconds} сек"
    if task.status == ExportStatus.done:
        content = (
            f"Глубокий анализ завершён за {duration}. Обработано {task.org_count} компаний: "
            f"успешно {details.get('successful_count', 0)}, частично {details.get('partial_count', 0)}, "
            f"с ошибками {details.get('failed_count', 0)}. Справа доступен предпросмотр и файлы."
        )
    else:
        content = f"Глубокий анализ завершился с ошибкой. {task.error or 'Данные не получены.'}"
    db.add(ChatMessage(
        session_id=session.id,
        role="assistant",
        content=content,
        metadata_json=json.dumps({
            "type": "company_analysis_completion",
            "task_id": task.id,
            "status": task.status.value,
            "elapsed_seconds": elapsed,
            "action": "open_company_analysis" if task.status == ExportStatus.done else None,
        }, ensure_ascii=False),
    ))
    task.completion_notified = True
    db.commit()


def _analysis_result_for_api(task: CompanyAnalysisTask) -> dict:
    result = _json_value(task.result_json, {})
    for company in result.get("companies") or []:
        business_id = company.get("business_id")
        photos = []
        for raw_photo in company.get("photos") or []:
            photo = str(raw_photo)
            if photo.startswith(("http://", "https://")):
                photos.append(photo)
            elif business_id and Path(photo).name:
                photos.append(
                    f"/api/parser/company-analysis/{task.id}/photos/{business_id}/{Path(photo).name}"
                )
        company["photos"] = photos
    return result


@router.get("/company-analysis/{task_id}/status")
async def company_analysis_status(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    task = db.get(CompanyAnalysisTask, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача анализа не найдена")
    _ensure_analysis_completion_message(db, task)
    response = {
        "id": task.id,
        "status": task.status.value,
        "progress": task.progress,
        "message": task.message,
        "error": task.error,
        "summary_error": task.summary_error,
        "org_count": task.org_count,
        "quota_charged": task.quota_charged,
        "progress_details": _json_value(task.progress_json, {}),
        "options": _json_value(task.options_json, {}),
    }
    if task.result_json:
        response["result"] = _analysis_result_for_api(task)
    if task.summary_json:
        response["summary"] = _json_value(task.summary_json, {})
    if task.xlsx_download_token:
        response["xlsx_download_url"] = (
            f"/api/parser/company-analysis/download?token={task.xlsx_download_token}&format=xlsx"
        )
    if task.zip_download_token:
        response["zip_download_url"] = (
            f"/api/parser/company-analysis/download?token={task.zip_download_token}&format=zip"
        )
    return response


@router.get("/company-analysis/tasks")
async def company_analysis_tasks(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    tasks = db.execute(
        select(CompanyAnalysisTask)
        .where(CompanyAnalysisTask.user_id == user.id)
        .order_by(CompanyAnalysisTask.created_at.desc())
        .limit(100)
    ).scalars().all()
    return [
        {
            "id": task.id,
            "status": task.status.value,
            "progress": task.progress,
            "message": task.message,
            "error": task.error,
            "org_count": task.org_count,
            "quota_charged": task.quota_charged,
            "options": _json_value(task.options_json, {}),
            "chat_session_id": task.chat_session_id,
            "created_at": task.created_at.isoformat(),
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "xlsx_download_url": (
                f"/api/parser/company-analysis/download?token={task.xlsx_download_token}&format=xlsx"
                if task.xlsx_download_token else None
            ),
            "zip_download_url": (
                f"/api/parser/company-analysis/download?token={task.zip_download_token}&format=zip"
                if task.zip_download_token else None
            ),
        }
        for task in tasks
    ]


@router.get("/company-analysis/{task_id}/photos/{business_id}/{filename}")
async def company_analysis_photo(
    task_id: str,
    business_id: int,
    filename: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    task = db.get(CompanyAnalysisTask, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Фото не найдено")
    allowed_ids = {int(value) for value in _json_value(task.business_ids_json, [])}
    if business_id not in allowed_ids or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Фото не найдено")
    root = (Path(settings.export_dir) / f"company_analysis_{task.id}" / str(business_id) / "photos").resolve()
    photo = (root / filename).resolve()
    if root not in photo.parents or not photo.is_file():
        raise HTTPException(status_code=404, detail="Фото не найдено")
    return FileResponse(photo)


@router.post("/company-analysis/{task_id}/retry-summary")
async def retry_company_analysis_summary(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    task = db.get(CompanyAnalysisTask, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача анализа не найдена")
    if task.status != ExportStatus.done or not task.result_json:
        raise HTTPException(status_code=409, detail="Данные анализа ещё не готовы")
    ok = await regenerate_company_analysis_summary(db, task)
    if not ok:
        raise HTTPException(status_code=503, detail=task.summary_error or "LLM не вернул сводку")
    return {"summary": _json_value(task.summary_json, {}), "summary_error": None}


@router.get("/company-analysis/download")
async def download_company_analysis(
    token: str = Query(...),
    format: str = Query("zip", pattern="^(xlsx|zip)$"),  # noqa: A002
    db: Session = Depends(get_db),
):
    token_column = (
        CompanyAnalysisTask.xlsx_download_token
        if format == "xlsx" else CompanyAnalysisTask.zip_download_token
    )
    task = db.execute(select(CompanyAnalysisTask).where(token_column == token)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Файл анализа не найден")
    download_id = task.xlsx_download_id if format == "xlsx" else task.zip_download_id
    download = db.get(Download, download_id) if download_id else None
    if not download or not download.file_path or not os.path.exists(download.file_path):
        raise HTTPException(status_code=410, detail="Файл анализа недоступен")
    filename = f"company_analysis_{task.id[:8]}.{format}"
    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if format == "xlsx" else "application/zip"
    )
    return FileResponse(download.file_path, filename=filename, media_type=media_type)


@router.get("/tasks")
async def parser_tasks_history(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.execute(
        select(ParserTask)
        .where(ParserTask.user_id == user.id)
        .order_by(ParserTask.created_at.desc())
        .limit(20)
    ).scalars().all()
    out = []
    for t in rows:
        try:
            params = json.loads(t.params_json or "{}")
        except json.JSONDecodeError:
            params = {}
        out.append({
            "id": t.id,
            "status": t.status.value,
            "progress": t.progress,
            "error": t.error,
            "kind": t.kind,
            "source": t.source,
            "query": (
                "; ".join(f"{s.get('query','')} · {s.get('location','')}".strip(" ·")
                          for s in (params.get("searches") or []))
                or params.get("query") or params.get("url")
            ),
            "org_count": t.org_count,
            "credits_charged": t.credits_charged,
            "created_at": t.created_at.isoformat(),
            "download_url": (
                f"/api/parser/download?token={t.download_token}"
                if t.status == ExportStatus.done and t.download_token else None
            ),
        })
    return out


@router.get("/download")
async def download_parser_result(
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    task = db.execute(
        select(ParserTask).where(ParserTask.download_token == token)
    ).scalar_one_or_none()
    if not task or not task.download_id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    dl = db.get(Download, task.download_id)
    if not dl or not dl.file_path or not os.path.exists(dl.file_path):
        raise HTTPException(status_code=410, detail="Файл удалён или ещё не готов")
    if dl.expires_at < utcnow():
        raise HTTPException(status_code=410, detail="Срок действия ссылки истёк")

    is_zip = dl.file_path.lower().endswith(".zip")
    if is_zip:
        prefix = "company_analysis" if task.kind == "company_analysis" else "landing"
        filename = f"{prefix}_{task.id[:8]}.zip"
        media_type = "application/zip"
    else:
        filename = _xlsx_filename(task)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(path=dl.file_path, filename=filename, media_type=media_type)


def _xlsx_filename(task: ParserTask) -> str:
    """Имя файла из запроса и активных фильтров, напр. «банкротство_Татарстан_без-сайта.xlsx»."""
    import re as _re
    try:
        p = json.loads(task.params_json or "{}")
    except json.JSONDecodeError:
        p = {}
    searches = p.get("searches") or []
    parts: list[str] = []
    if searches:
        s0 = searches[0]
        parts.append(str(s0.get("query") or "").strip())
        parts.append(str(s0.get("location") or "").strip())
        if len(searches) > 1:
            parts.append(f"и-ещё-{len(searches) - 1}")
    tags = []
    if p.get("f_phone") == "yes":
        tags.append("с-телефоном")
    if p.get("f_phone") == "no":
        tags.append("без-телефона")
    if p.get("f_site") == "yes":
        tags.append("с-сайтом")
    if p.get("f_site") == "no":
        tags.append("без-сайта")
    if p.get("f_franchise") == "yes":
        tags.append("франшизы")
    if p.get("f_franchise") == "no":
        tags.append("независимые")
    for net in (p.get("social_nets") or []):
        tags.append(str(net))
    if p.get("reviews_min"):
        tags.append(f"отзывов-от-{p['reviews_min']}")
    if p.get("rating_min"):
        tags.append(f"рейтинг-от-{p['rating_min']}")
    name = "_".join([x for x in parts + tags if x]) or f"parser_{task.id[:8]}"
    name = _re.sub(r"[^\w\-а-яА-ЯёЁ]+", "-", name).strip("-_")[:120]
    return f"{name or 'parser'}.xlsx"
