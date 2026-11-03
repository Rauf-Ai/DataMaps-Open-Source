from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Тонкий клиент Telegram Bot API через httpx.

Используется ИИ-агентом поддержки, чтобы:
  * присылать владельцу запрос на подтверждение действия (inline-кнопки);
  * слать баг-репорты;
  * уведомлять об эскалациях.

Если TELEGRAM_BOT_TOKEN не задан — все функции становятся no-op (с логом),
чтобы dev-окружение и тесты не падали.
"""

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_TIMEOUT = 15


def _api_url(method: str) -> Optional[str]:
    settings = get_settings()
    token = settings.telegram_bot_token
    if not token:
        return None
    return f"https://api.telegram.org/bot{token}/{method}"


def is_configured() -> bool:
    s = get_settings()
    return bool(s.telegram_bot_token and s.telegram_admin_chat_id)


def verify_secret(header_value: Optional[str]) -> bool:
    """Сверяет заголовок X-Telegram-Bot-Api-Secret-Token с настроенным секретом.

    Если секрет не задан в конфиге — считаем webhook незащищённым и отклоняем
    (безопасный дефолт: без секрета webhook не работает).
    """
    secret = get_settings().telegram_webhook_secret
    if not secret:
        return False
    return header_value == secret


def is_admin_chat(chat_id) -> bool:
    return str(chat_id) == str(get_settings().telegram_admin_chat_id)


async def _post(method: str, payload: dict) -> Optional[dict]:
    url = _api_url(method)
    if not url:
        log.info("telegram %s пропущен: TELEGRAM_BOT_TOKEN не задан", method)
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
        data = resp.json()
        if not data.get("ok"):
            log.warning("telegram %s вернул ошибку: %s", method, data)
        return data
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("telegram %s исключение: %s", method, exc)
        return None


async def send_message(text: str, reply_markup: Optional[dict] = None,
                       chat_id: Optional[str] = None) -> Optional[dict]:
    """Отправить сообщение владельцу (по умолчанию — в admin chat)."""
    settings = get_settings()
    target = chat_id or settings.telegram_admin_chat_id
    if not target:
        log.info("telegram send_message пропущен: нет TELEGRAM_ADMIN_CHAT_ID")
        return None
    payload: dict = {
        "chat_id": target,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await _post("sendMessage", payload)


def _approval_keyboard(action_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Подтвердить", "callback_data": f"pa:{action_id}:approve"},
            {"text": "❌ Отклонить", "callback_data": f"pa:{action_id}:reject"},
        ]]
    }


async def send_approval(action_id: str, text: str) -> Optional[int]:
    """Прислать запрос на подтверждение действия с кнопками.

    Возвращает message_id отправленного сообщения (для последующего edit) или None.
    """
    data = await send_message(text, reply_markup=_approval_keyboard(action_id))
    if data and data.get("ok"):
        return data.get("result", {}).get("message_id")
    return None


async def send_bug_report(title: str, body: str) -> Optional[dict]:
    text = f"🐞 <b>Баг-репорт</b>\n\n<b>{title}</b>\n\n{body}"
    return await send_message(text)


async def answer_callback(callback_id: str, text: str = "") -> Optional[dict]:
    return await _post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})


async def edit_message(message_id: int, text: str, chat_id: Optional[str] = None) -> Optional[dict]:
    settings = get_settings()
    target = chat_id or settings.telegram_admin_chat_id
    if not target or not message_id:
        return None
    return await _post("editMessageText", {
        "chat_id": target,
        "message_id": message_id,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
