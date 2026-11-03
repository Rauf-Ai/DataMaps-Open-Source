from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Единый формат диагностических ошибок API.

Код ошибки короткий и стабильный: его удобно прислать в поддержку или искать в логах.
"""


from fastapi import (
    HTTPException
)


def error_detail(
    code: str,
    message: str,
    *,
    hint: Optional[str] = None,
    request_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {"code": code, "message": message}
    if hint:
        detail["hint"] = hint
    if request_id:
        detail["request_id"] = request_id
    if extra:
        detail["extra"] = extra
    return detail


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    hint: Optional[str] = None,
    request_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=error_detail(code, message, hint=hint, request_id=request_id, extra=extra),
    )


def classify_ai_error(raw: str) -> tuple[int, str, str, Optional[str]]:
    """Маппинг текстовых ошибок LLM/AI в стабильные диагностические коды."""
    text = (raw or "").strip()
    low = text.lower()

    if "дерево категорий" in low:
        return 503, "AI-101", "Не загружено дерево категорий", "Запустите импорт CSV и перезапустите backend."
    if ("llm_api_key" in low or "api_key" in low) and "не настроен" in low:
        return 503, "AI-201", "LLM API key не настроен", "Проверьте LLM_API_KEY в .env и перезапустите backend."
    if "security policy" in low or "access denied" in low:
        return (
            502,
            "AI-202",
            "LLM-провайдер заблокировал запрос политикой безопасности",
            "OpenRouter вернул Access denied by security policy. Проверьте ключ/аккаунт OpenRouter или смените LLM_BASE_URL на другого провайдера.",
        )
    if "авториза" in low or "401" in low or "403" in low:
        return 502, "AI-202", "Ошибка авторизации LLM", "Проверьте LLM_API_KEY, провайдера и модель."
    if "rate-limit" in low or "перегружен" in low or "429" in low:
        return 503, "AI-203", "LLM временно ограничил запросы", "Повторите через минуту или смените модель/провайдера."
    if "402" in low or "квот" in low or "credits" in low or "insufficient" in low:
        return 502, "AI-204", "У LLM-провайдера нет доступной квоты", "Пополните баланс у провайдера или смените LLM_MODEL."
    if "timeout" in low:
        return 504, "AI-205", "LLM не ответил за отведённое время", "Увеличьте LLM_TIMEOUT или повторите запрос."
    if "connection error" in low or "connect" in low or "network" in low:
        return 502, "AI-206", "Ошибка соединения с LLM", "Проверьте LLM_BASE_URL и доступность провайдера."
    if "не-json" in low or "не json" in low:
        return 502, "AI-207", "LLM вернул ответ не в JSON", "Повторите запрос или смените модель на более стабильную."
    if "bad-request" in low or "400" in low:
        return 502, "AI-208", "LLM отклонил формат запроса", "Проверьте модель и поддержку JSON response_format."
    if "server" in low or "5" in low[:1]:
        return 502, "AI-209", "Ошибка на стороне LLM-провайдера", "Повторите позже или смените провайдера."
    if "unexpected response shape" in low:
        return 502, "AI-210", "Неожиданный формат ответа LLM", "Нужно проверить сырой ответ провайдера в логах."

    return 502, "AI-299", "AI не смог обработать запрос", text or "Неизвестная ошибка AI."
