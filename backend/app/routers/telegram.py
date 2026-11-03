from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Webhook Telegram-бота: подтверждение/отклонение действий агента поддержки.

Безопасность:
  * обязательная проверка заголовка X-Telegram-Bot-Api-Secret-Token;
  * реагируем только на нажатия от admin chat_id.
"""

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request
)
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import PendingAction
from app.services import support_service, telegram_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not telegram_service.verify_secret(secret):
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        update = await request.json()
    except ValueError:
        return {"ok": True}

    cq = update.get("callback_query")
    if cq:
        await _handle_callback(db, cq)
    return {"ok": True}


async def _handle_callback(db: Session, cq: dict) -> None:
    callback_id = cq.get("id")
    from_id = cq.get("from", {}).get("id")
    if not telegram_service.is_admin_chat(from_id):
        await telegram_service.answer_callback(callback_id, "Недостаточно прав")
        return

    data = cq.get("data") or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "pa":
        await telegram_service.answer_callback(callback_id, "Непонятная команда")
        return
    _, pa_id, decision = parts

    pa = db.get(PendingAction, pa_id)
    if not pa:
        await telegram_service.answer_callback(callback_id, "Действие не найдено")
        return

    msg_id = cq.get("message", {}).get("message_id")
    summary = pa.summary or pa.kind

    if decision == "approve":
        ok, result = support_service.execute_pending_action(db, pa)
        await telegram_service.answer_callback(callback_id, result[:200])
        prefix = "✅" if ok else "⚠️"
        if msg_id:
            await telegram_service.edit_message(msg_id, f"{prefix} {summary}\n{result}")
    elif decision == "reject":
        support_service.reject_pending_action(db, pa)
        await telegram_service.answer_callback(callback_id, "Отклонено")
        if msg_id:
            await telegram_service.edit_message(msg_id, f"❌ Отклонено: {summary}")
    else:
        await telegram_service.answer_callback(callback_id, "Неизвестное решение")
