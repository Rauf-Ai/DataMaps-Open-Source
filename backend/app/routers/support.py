from typing import Optional, Any, Dict, List, Literal, Union, Callable

from fastapi import UploadFile
"""Эндпоинты чата техподдержки: история, отправка сообщения агенту, загрузка фото.

Доступно залогиненным и гостям (get_optional_user). Гость опознаётся cookie
`support_guest_id`. Жёсткий rate-limit + дневной лимит сообщений для гостей.

ВАЖНО: без `from __future__ import annotations` — иначе FastAPI не распознаёт
параметр UploadFile (аннотации становятся строками и падает регистрация /upload).
"""
import os
import uuid
from datetime import timedelta

from fastapi import (
    APIRouter,
    Body,
    Cookie,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_optional_user
from app.models import SupportConversation, SupportMessage, User
from app.ratelimit import limiter
from app.security import generate_token
from app.services import support_service
from app.timeutils import utcnow

settings = get_settings()
router = APIRouter(prefix="/api/support", tags=["support"])

_GUEST_COOKIE = "support_guest_id"
_MAX_LEN = 2000
_IMG_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def _load_conversation(db: Session, conv_id: Optional[int], user: Optional[User],
                       guest_id: Optional[str]) -> Optional[SupportConversation]:
    """Грузит беседу с проверкой владельца. None — если не найдена/чужая."""
    if not conv_id:
        return None
    conv = db.get(SupportConversation, conv_id)
    if not conv:
        return None
    if user is not None:
        return conv if conv.user_id == user.id else None
    return conv if (guest_id and conv.guest_id == guest_id) else None


def _serialize_messages(rows) -> list[dict]:
    out = []
    for m in rows:
        d = {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
        if m.image_path:
            d["image_url"] = f"/api/support/attachment/{m.id}"
        out.append(d)
    return out


def _guest_messages_24h(db: Session, guest_id: str) -> int:
    since = utcnow() - timedelta(hours=24)
    return db.execute(
        select(func.count(SupportMessage.id))
        .join(SupportConversation, SupportConversation.id == SupportMessage.conversation_id)
        .where(SupportConversation.guest_id == guest_id,
               SupportMessage.role == "user",
               SupportMessage.created_at >= since)
    ).scalar_one()


def _set_guest_cookie(response: Response, guest_id: str) -> None:
    response.set_cookie(
        _GUEST_COOKIE, guest_id, max_age=90 * 86400,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")


@router.post("/message")
@limiter.limit("15/minute")
async def support_message(
    request: Request,
    response: Response,
    payload: dict = Body(...),
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
    support_guest_id: Optional[str] = Cookie(default=None),
):
    text = (payload.get("message") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Введите сообщение")
    if len(text) > _MAX_LEN:
        raise HTTPException(status_code=400, detail=f"Сообщение слишком длинное (>{_MAX_LEN})")

    guest_id = None
    if user is None:
        guest_id = support_guest_id or generate_token(16)
        if _guest_messages_24h(db, guest_id) >= settings.support_guest_daily_limit:
            raise HTTPException(
                status_code=429,
                detail="Дневной лимит сообщений для гостей исчерпан. Войдите, чтобы продолжить.")
        if not support_guest_id:
            _set_guest_cookie(response, guest_id)

    conv = _load_conversation(db, payload.get("conversation_id"), user, guest_id)
    if conv is None:
        conv = SupportConversation(user_id=user.id if user else None, guest_id=guest_id, status="open")
        db.add(conv)
        db.flush()
    elif conv.status == "closed":
        conv.status = "open"  # новое сообщение в закрытый диалог — открываем заново

    db.add(SupportMessage(conversation_id=conv.id, role="user", content=text))
    conv.last_message_at = utcnow()
    db.commit()  # фиксируем сообщение пользователя ДО вызова LLM (не держим блокировку SQLite)

    result = await support_service.handle_message(db, conv, user, text)
    result["conversation_id"] = conv.id
    return result


@router.post("/upload")
@limiter.limit("10/minute")
async def support_upload(
    request: Request,
    response: Response,
    file: Any = File(...),
    conversation_id: Optional[int] = Form(default=None),
    caption: str = Form(default=""),
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
    support_guest_id: Optional[str] = Cookie(default=None),
):
    ct = (file.content_type or "").lower()
    if not ct.startswith("image/"):
        raise HTTPException(status_code=400, detail="Можно загружать только изображения")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > settings.support_max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413,
                            detail=f"Файл слишком большой (макс {settings.support_max_upload_mb} МБ)")

    guest_id = None
    if user is None:
        guest_id = support_guest_id or generate_token(16)
        if _guest_messages_24h(db, guest_id) >= settings.support_guest_daily_limit:
            raise HTTPException(status_code=429,
                                detail="Дневной лимит для гостей исчерпан. Войдите, чтобы продолжить.")
        if not support_guest_id:
            _set_guest_cookie(response, guest_id)

    conv = _load_conversation(db, conversation_id, user, guest_id)
    if conv is None:
        conv = SupportConversation(user_id=user.id if user else None, guest_id=guest_id, status="open")
        db.add(conv)
        db.flush()

    ext = _IMG_EXT.get(ct) or os.path.splitext(file.filename or "")[1][:8] or ".img"
    os.makedirs(settings.support_upload_dir, exist_ok=True)
    fpath = os.path.join(settings.support_upload_dir, f"{conv.id}_{uuid.uuid4().hex}{ext}")
    with open(fpath, "wb") as f:
        f.write(data)

    db.add(SupportMessage(conversation_id=conv.id, role="user",
                          content=(caption or "").strip()[:500], image_path=fpath))
    db.add(SupportMessage(conversation_id=conv.id, role="assistant",
                          content="Фото получил и передал оператору — он посмотрит и ответит."))
    conv.status = "needs_human"
    conv.last_message_at = utcnow()
    db.commit()

    await support_service.escalate_to_human(
        conv.id, user.email if user else None, "Пользователь приложил фото к обращению.")
    return {"conversation_id": conv.id, "needs_human": True}


@router.get("/attachment/{message_id}")
async def support_attachment(
    message_id: int,
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
    support_guest_id: Optional[str] = Cookie(default=None),
):
    msg = db.get(SupportMessage, message_id)
    if not msg or not msg.image_path:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    conv = db.get(SupportConversation, msg.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    allowed = (
        (user is not None and (user.is_admin or conv.user_id == user.id))
        or (support_guest_id and conv.guest_id == support_guest_id)
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Нет доступа")
    if not os.path.exists(msg.image_path):
        raise HTTPException(status_code=410, detail="Файл удалён")
    return FileResponse(msg.image_path)


@router.get("/latest")
async def support_latest(
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
    support_guest_id: Optional[str] = Cookie(default=None),
):
    """Последняя беседа вызывающего (для восстановления чата после перезагрузки)."""
    stmt = select(SupportConversation).order_by(SupportConversation.last_message_at.desc()).limit(1)
    if user is not None:
        stmt = stmt.where(SupportConversation.user_id == user.id)
    elif support_guest_id:
        stmt = stmt.where(SupportConversation.guest_id == support_guest_id)
    else:
        return {"conversation_id": None, "messages": []}
    conv = db.execute(stmt).scalar_one_or_none()
    if conv is None:
        return {"conversation_id": None, "messages": []}
    rows = db.execute(
        select(SupportMessage).where(SupportMessage.conversation_id == conv.id)
        .order_by(SupportMessage.created_at)
    ).scalars().all()
    return {"conversation_id": conv.id, "status": conv.status, "messages": _serialize_messages(rows)}


@router.get("/my-conversations")
async def support_my_conversations(
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
    support_guest_id: Optional[str] = Cookie(default=None),
):
    """История обращений вызывающего (для кабинета пользователя)."""
    stmt = select(SupportConversation).order_by(SupportConversation.last_message_at.desc()).limit(50)
    if user is not None:
        stmt = stmt.where(SupportConversation.user_id == user.id)
    elif support_guest_id:
        stmt = stmt.where(SupportConversation.guest_id == support_guest_id)
    else:
        return []
    convs = db.execute(stmt).scalars().all()
    out = []
    for c in convs:
        last = db.execute(
            select(SupportMessage).where(SupportMessage.conversation_id == c.id)
            .order_by(SupportMessage.created_at.desc()).limit(1)
        ).scalars().first()
        out.append({
            "id": c.id,
            "status": c.status,
            "last_message_at": c.last_message_at.isoformat(),
            "snippet": (last.content[:120] if last and last.content else
                        ("[фото]" if last and last.image_path else "")),
        })
    return out


@router.post("/conversation/{conv_id}/close")
async def support_close(
    conv_id: int,
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
    support_guest_id: Optional[str] = Cookie(default=None),
):
    conv = _load_conversation(db, conv_id, user, support_guest_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    conv.status = "closed"
    db.commit()
    return {"ok": True, "status": "closed"}


@router.get("/conversation/{conv_id}")
async def support_conversation(
    conv_id: int,
    user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
    support_guest_id: Optional[str] = Cookie(default=None),
):
    conv = _load_conversation(db, conv_id, user, support_guest_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    rows = db.execute(
        select(SupportMessage).where(SupportMessage.conversation_id == conv.id)
        .order_by(SupportMessage.created_at)
    ).scalars().all()
    return {
        "conversation_id": conv.id,
        "status": conv.status,
        "messages": _serialize_messages(rows),
    }
