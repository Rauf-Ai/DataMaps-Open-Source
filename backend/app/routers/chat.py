from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable, Annotated

import asyncio
import json
import logging
import uuid

from fastapi import (
    UploadFile,
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import get_settings
from app.deps import get_current_user
from app.error_codes import api_error, classify_ai_error
from app.models import (
    Business,
    ChatMessage,
    ChatSession,
    CompanyAnalysisTask,
    ExportStatus,
    ParserTask,
    SavedResult,
    SavedResultItem,
    User,
)
from app.routers.parser import _parse_search_payload, _require_verified
from app.services.chat_search_service import default_live_plan, refine_live_plan
from app.services.company_analysis_service import (
    new_company_analysis_task_id,
    normalize_company_analysis_options,
    run_company_analysis_task,
)
from app.services.deep_analysis.channel_discovery import available_channel_ids, discover_company_channels
from app.services.email_service import send_llm_failure_alert
from app.services.maps_service import map_source_configuration_error, new_task_id, run_parser_task
from app.services.subscription_service import ensure_task_slot, get_or_create_subscription, quota_available
from app.services.stt_service import transcribe_audio
from app.timeutils import utcnow


log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])
settings = get_settings()


@router.post("/transcribe")
async def transcribe_voice_query(
    audio: Annotated[UploadFile, File(...)],
    user: Annotated[User, Depends(get_current_user)],
):
    del user  # Authentication is the access boundary; transcription is not persisted here.
    try:
        text = await transcribe_audio(await audio.read(), audio.content_type or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        message = str(exc)
        status = 504 if "STT-205" in message else 503
        raise HTTPException(status_code=status, detail=message) from exc
    return {"text": text, "model": settings.stt_model}


def _json_object(raw: Optional[str], default: Optional[dict] = None) -> dict:
    if not raw:
        return default.copy() if default is not None else {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else (default.copy() if default is not None else {})
    except (TypeError, json.JSONDecodeError):
        return default.copy() if default is not None else {}


def _message_content(value: Any) -> str:
    """Keep the chat API and database contract string-only for legacy AI payloads."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _message_dict(message: ChatMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": _message_content(message.content),
        "metadata": _json_object(message.metadata_json),
        "created_at": message.created_at.isoformat(),
    }


def _messages(db: Session, session_id: str) -> list[ChatMessage]:
    return list(
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        ).scalars()
    )


def _session_summary(session: ChatSession) -> dict:
    return {
        "id": session.id,
        "title": session.title,
        "has_task": bool(session.parser_task_id),
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


def _session_detail(db: Session, session: ChatSession) -> dict:
    analysis_task = db.execute(
        select(CompanyAnalysisTask)
        .where(CompanyAnalysisTask.chat_session_id == session.id)
        .order_by(CompanyAnalysisTask.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {
        **_session_summary(session),
        "plan": _json_object(session.search_state_json, default_live_plan()),
        "parser_task_id": session.parser_task_id,
        "company_analysis_task_id": analysis_task.id if analysis_task else None,
        "messages": [_message_dict(message) for message in _messages(db, session.id)],
    }


def _owned_session(db: Session, user_id: int, session_id: str) -> ChatSession:
    session = db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
            ChatSession.archived_at.is_(None),
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return session


def _normalized_plan(payload: dict) -> dict:
    source, max_per, params = _parse_search_payload(payload)
    return {"source": source, "max": max_per, **params}


def _analysis_selection(db: Session, user_id: int, saved_result_id: int, raw_ids) -> list[Business]:
    saved = db.get(SavedResult, saved_result_id)
    if not saved or saved.user_id != user_id:
        raise HTTPException(status_code=404, detail="Сохранённый результат не найден")
    try:
        selected = list(dict.fromkeys(int(value) for value in (raw_ids or [])))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректные ID организаций") from exc
    if not selected:
        raise HTTPException(status_code=400, detail="Не выбрано ни одной организации")
    if len(selected) > 20:
        raise HTTPException(status_code=400, detail="За один запуск можно анализировать максимум 20 организаций")
    allowed = set(db.execute(
        select(SavedResultItem.business_id).where(
            SavedResultItem.saved_result_id == saved_result_id,
            SavedResultItem.business_id.in_(selected),
        )
    ).scalars().all())
    if allowed != set(selected):
        raise HTTPException(status_code=400, detail="Выбраны организации не из этого результата")
    businesses = db.execute(select(Business).where(Business.id.in_(selected))).scalars().all()
    by_id = {business.id: business for business in businesses}
    return [by_id[value] for value in selected]


async def _analysis_channel_metadata(businesses: list[Business]) -> dict:
    discoveries = await asyncio.gather(*(
        discover_company_channels({
            "business_id": business.id,
            "website": business.website,
            "social": business.social,
        })
        for business in businesses
    ))
    records = {key: [] for key in ("website", "telegram", "vk")}
    for discovery in discoveries:
        for key in records:
            records[key].extend(discovery.get(key) or [])
    return {
        "available_channels": available_channel_ids(records),
        "unavailable_channels": [
            row for key in records for row in records[key]
            if row.get("status") not in {"available", "missing"}
        ],
    }


def _ensure_search_completion_message(db: Session, session: ChatSession, task: ParserTask) -> None:
    existing = _messages(db, session.id)
    for message in existing:
        metadata = _json_object(message.metadata_json)
        if metadata.get("type") == "search_completion" and metadata.get("task_id") == task.id:
            return
    params = _json_object(task.params_json)
    progress = _json_object(task.progress_json)
    target = int(progress.get("target_count") or params.get("target_count") or params.get("max") or task.org_count)
    elapsed = max(0, int((task.updated_at - task.created_at).total_seconds())) if task.updated_at and task.created_at else 0
    minutes, seconds = divmod(elapsed, 60)
    duration = f"{minutes} мин {seconds} сек" if minutes else f"{seconds} сек"
    content = (
        f"Поиск завершён за {duration}. Найдено {task.org_count} из {target} организаций. "
        "Откройте результат, чтобы скачать XLSX или выбрать компании и запустить глубокий анализ."
    )
    result = _json_object(task.result_json)
    saved_result_id = result.get("saved_result_id")
    db.add(ChatMessage(
        session_id=session.id,
        role="assistant",
        content=content,
        metadata_json=json.dumps({
            "type": "search_completion",
            "task_id": task.id,
            "found_count": task.org_count,
            "target_count": target,
            "elapsed_seconds": elapsed,
            "saved_result_id": saved_result_id,
            "action": "open_search_result",
        }, ensure_ascii=False),
    ))
    session.updated_at = utcnow()
    db.commit()


@router.get("/sessions")
async def list_sessions(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    sessions = db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user.id, ChatSession.archived_at.is_(None))
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
    ).scalars().all()
    return [_session_summary(session) for session in sessions]


@router.post("/sessions")
async def create_session(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    session = ChatSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        title="Новый запрос",
        search_state_json=json.dumps(default_live_plan(), ensure_ascii=False),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_detail(db, session)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return _session_detail(db, _owned_session(db, user.id, session_id))


@router.post("/sessions/{session_id}/messages")
async def add_message(
    session_id: str,
    request: Request,
    background: BackgroundTasks,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _owned_session(db, user.id, session_id)
    content = str(payload.get("content") or "").strip()
    request_id = getattr(request.state, "request_id", None)
    if not content:
        raise api_error(400, "CHAT-001", "Введите сообщение", request_id=request_id)
    if len(content) > 2000:
        raise api_error(
            400,
            "CHAT-002",
            "Сообщение слишком длинное",
            hint="Максимум 2000 символов.",
            request_id=request_id,
        )

    db.add(ChatMessage(session_id=session.id, role="user", content=content))
    if session.title == "Новый запрос":
        session.title = content[:64]
    session.updated_at = utcnow()
    db.commit()

    history = [
        {"role": message.role, "content": message.content}
        for message in _messages(db, session.id)
        if message.role in {"user", "assistant"}
    ]
    previous = _json_object(session.search_state_json, default_live_plan())
    try:
        result = await refine_live_plan(history, previous)
    except RuntimeError as exc:
        status, code, message, hint = classify_ai_error(str(exc))
        log.warning("chat refine failed code=%s request_id=%s error=%s", code, request_id, exc)
        raise api_error(status, code, message, hint=hint, request_id=request_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("chat refine unexpected code=CHAT-500 request_id=%s", request_id)
        raise api_error(
            500,
            "CHAT-500",
            "Не удалось обработать сообщение",
            hint="Повторите запрос или сообщите ID ошибки поддержке.",
            request_id=request_id,
            extra={"type": exc.__class__.__name__},
        )

    session.search_state_json = json.dumps(result["plan"], ensure_ascii=False)
    session.updated_at = utcnow()
    if result.get("fallback_used") and result.get("llm_error"):
        background.add_task(
            send_llm_failure_alert,
            str(result["llm_error"]),
            request_id,
            user.id,
        )
    db.add(
        ChatMessage(
            session_id=session.id,
            role="assistant",
            content=_message_content(result["assistant_message"]),
            metadata_json=json.dumps(
                {
                    "reasoning": result["reasoning"],
                    "unknown": result.get("unknown") or {},
                    "plan_version": 1,
                    "fallback_used": bool(result.get("fallback_used")),
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    db.refresh(session)
    return _session_detail(db, session)


@router.patch("/sessions/{session_id}/plan")
async def update_plan(
    session_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _owned_session(db, user.id, session_id)
    plan = _normalized_plan(payload)
    session.search_state_json = json.dumps(plan, ensure_ascii=False)
    session.updated_at = utcnow()
    db.commit()
    db.refresh(session)
    return _session_detail(db, session)


@router.post("/sessions/{session_id}/analysis-intent")
async def create_analysis_intent(
    session_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _owned_session(db, user.id, session_id)
    saved_result_id = int(payload.get("saved_result_id") or 0)
    businesses = _analysis_selection(db, user.id, saved_result_id, payload.get("business_ids"))
    channel_metadata = await _analysis_channel_metadata(businesses)
    business_ids = [business.id for business in businesses]
    names = [business.name for business in businesses[:3]]
    suffix = f" и ещё {len(businesses) - 3}" if len(businesses) > 3 else ""
    db.add(ChatMessage(
        session_id=session.id,
        role="user",
        content=f"Проведи глубокий анализ {len(businesses)} компаний: {', '.join(names)}{suffix}.",
        metadata_json=json.dumps({
            "type": "company_analysis_request",
            "saved_result_id": saved_result_id,
            "business_ids": business_ids,
        }, ensure_ascii=False),
    ))
    db.add(ChatMessage(
        session_id=session.id,
        role="assistant",
        content="Уточните, какие данные собрать. Настройки применятся ко всем выбранным компаниям.",
        metadata_json=json.dumps({
            "type": "company_analysis_config",
            "saved_result_id": saved_result_id,
            "business_ids": business_ids,
            "business_count": len(businesses),
            **channel_metadata,
            "options": {
                "photos": True,
                "reviews": True,
                "review_count": 50,
                "review_rating_min": 1,
                "review_rating_max": 5,
                "services_prices": True,
                "website": False,
                "telegram": False,
                "vk": False,
            },
        }, ensure_ascii=False),
    ))
    session.updated_at = utcnow()
    db.commit()
    db.refresh(session)
    return _session_detail(db, session)


@router.post("/sessions/{session_id}/company-analysis")
async def start_company_analysis(
    session_id: str,
    background: BackgroundTasks,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _owned_session(db, user.id, session_id)
    _require_verified(user)
    saved_result_id = int(payload.get("saved_result_id") or 0)
    businesses = _analysis_selection(db, user.id, saved_result_id, payload.get("business_ids"))
    try:
        options = normalize_company_analysis_options(payload.get("options") or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    channel_metadata = await _analysis_channel_metadata(businesses)
    for key, label in (("website", "Сайт"), ("telegram", "Telegram"), ("vk", "ВКонтакте")):
        if options.get(key) and not channel_metadata["available_channels"][key]:
            raise HTTPException(status_code=400, detail=f"{label} не указан или недоступен для выбранных организаций")
    options["eligible_business_ids"] = channel_metadata["available_channels"]
    sub = get_or_create_subscription(db, user.id)
    try:
        ensure_task_slot(db, sub, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    available = quota_available(sub, user, "enrichment")
    if available < len(businesses):
        raise HTTPException(
            status_code=402,
            detail=f"Недостаточно операций анализа: нужно {len(businesses)}, доступно {available}",
        )
    task = CompanyAnalysisTask(
        id=new_company_analysis_task_id(),
        user_id=user.id,
        saved_result_id=saved_result_id,
        chat_session_id=session.id,
        status=ExportStatus.queued,
        progress=0,
        business_ids_json=json.dumps([business.id for business in businesses]),
        options_json=json.dumps(options, ensure_ascii=False),
        message="Анализ поставлен в очередь",
    )
    db.add(task)
    db.add(ChatMessage(
        session_id=session.id,
        role="system",
        content="Глубокий анализ запущен",
        metadata_json=json.dumps({
            "type": "company_analysis_started",
            "task_id": task.id,
        }, ensure_ascii=False),
    ))
    session.updated_at = utcnow()
    db.commit()
    background.add_task(run_company_analysis_task, task.id)
    return {"task_id": task.id, "org_count": len(businesses), "operations_required": len(businesses)}


@router.post("/sessions/{session_id}/search")
async def start_search(
    session_id: str,
    request: Request,
    background: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    session = _owned_session(db, user.id, session_id)
    _require_verified(user)
    plan = _json_object(session.search_state_json, default_live_plan())
    source, max_per, params = _parse_search_payload(plan)
    config_error = map_source_configuration_error(params, settings)
    if config_error:
        raise api_error(
            503,
            config_error["code"],
            config_error["message"],
            hint=config_error.get("hint"),
            request_id=getattr(request.state, "request_id", None),
        )
    sub = get_or_create_subscription(db, user.id)
    try:
        ensure_task_slot(db, sub, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    available = quota_available(sub, user, "organizations")
    if available < max_per:
        raise HTTPException(
            status_code=402,
            detail=f"Недостаточно доступного объёма: запрошено {max_per}, доступно {available}",
        )

    task = ParserTask(
        id=new_task_id(),
        user_id=user.id,
        status=ExportStatus.queued,
        progress=0,
        kind="live_search",
        source=source,
        params_json=json.dumps(params, ensure_ascii=False),
    )
    session.parser_task_id = task.id
    session.updated_at = utcnow()
    db.add(task)
    db.add(
        ChatMessage(
            session_id=session.id,
            role="system",
            content="Поиск запущен",
            metadata_json=json.dumps({"task_id": task.id}, ensure_ascii=False),
        )
    )
    db.commit()
    background.add_task(run_parser_task, task.id)
    return {"task_id": task.id, "available_organizations": available}


@router.get("/sessions/{session_id}/task")
async def get_task_status(
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    session = _owned_session(db, user.id, session_id)
    if not session.parser_task_id:
        raise HTTPException(status_code=404, detail="Поиск ещё не запускался")
    task = db.get(ParserTask, session.parser_task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    response = {
        "id": task.id,
        "status": task.status.value,
        "progress": task.progress,
        "error": task.error,
        "message": task.message,
        "org_count": task.org_count,
        "credits_charged": task.credits_charged,
    }
    progress = _json_object(task.progress_json)
    if progress:
        response.update(progress)
    if task.status == ExportStatus.done:
        _ensure_search_completion_message(db, session, task)
        if task.download_token:
            response["download_url"] = f"/api/parser/download?token={task.download_token}"
        result = _json_object(task.result_json)
        if isinstance(result.get("saved_result_id"), int):
            response["saved_result_id"] = result["saved_result_id"]
    return response


@router.delete("/sessions/{session_id}", status_code=204)
async def archive_session(
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    session = _owned_session(db, user.id, session_id)
    session.archived_at = utcnow()
    db.commit()
    return Response(status_code=204)
