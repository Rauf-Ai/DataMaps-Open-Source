from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""OpenAI-совместимый клиент для AI-сводок отзывов.

Конфигурируется через .env:
- LLM_BASE_URL (например, https://openrouter.ai/api/v1 или https://api.deepseek.com/v1)
- LLM_API_KEY
- LLM_MODEL
- LLM_TIMEOUT (секунды)
"""

import asyncio
import json
import logging

import httpx

from app.config import get_settings


log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Ты аналитик отзывов компаний. По набору отзывов сформируй структурированную сводку. "
    "Отвечай СТРОГО валидным JSON-объектом (без markdown-обёрток ```json и без пояснений) со следующими полями:\n"
    "  general: общее впечатление, 1-2 предложения (строка)\n"
    "  features: ключевые особенности заведения (строка, 1-2 предложения)\n"
    "  pros: главные плюсы (массив коротких строк, до 5)\n"
    "  cons: главные минусы (массив коротких строк, до 5)\n"
    "  service: одно предложение про сервис и персонал\n"
    "  speed: одно предложение про скорость обслуживания\n"
    "  prices: одно предложение про цены\n"
    "  quality: одно предложение про качество\n"
    "Если данных по какому-то полю недостаточно — пиши 'нет данных'. "
    "Отвечай ТОЛЬКО на русском языке."
)

_EMPTY_SUMMARY = {
    "general": "нет данных",
    "features": "нет данных",
    "pros": [],
    "cons": [],
    "service": "нет данных",
    "speed": "нет данных",
    "prices": "нет данных",
    "quality": "нет данных",
}


def _empty() -> dict[str, Any]:
    return dict(_EMPTY_SUMMARY)


def _build_user_prompt(org_name: str, address: str, rating: Optional[float], reviews: list[dict]) -> str:
    header = f'Организация: "{org_name}". Адрес: {address}. '
    if rating:
        header += f"Средний рейтинг: {rating:.1f}. "
    header += f"Всего отзывов в выборке: {len(reviews)}.\n\nОтзывы:\n"

    parts = []
    char_budget = 12000
    for i, r in enumerate(reviews[:50], 1):
        text = (r.get("text") or "").strip()
        if not text:
            continue
        stars = r.get("stars") or r.get("rating") or "?"
        chunk = f"\n[{i}] ★{stars}: {text}"
        if char_budget - len(chunk) < 0:
            break
        parts.append(chunk)
        char_budget -= len(chunk)

    return header + "".join(parts)


import re as _re


def _strip_think_blocks(s: str) -> str:
    """Удаляет reasoning-блоки <think>...</think> или <thinking>...</thinking>.

    Reasoning-модели (DeepSeek V4 Flash и т.п.) иногда вставляют рассуждения
    прямо в content в таких тегах. JSON-парсеру они мешают.
    """
    return _re.sub(r"<(think|thinking|reasoning)[^>]*>.*?</\1>", "", s, flags=_re.DOTALL | _re.IGNORECASE).strip()


def _extract_json(content: str) -> Optional[dict]:
    """Чистит markdown-обёртку ```json ... ``` и парсит JSON. None при неудаче."""
    if not content:
        return None
    s = _strip_think_blocks(content).strip()
    if s.startswith("```"):
        s = s.split("```", 2)[-1] if s.count("```") >= 2 else s.lstrip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    # Иногда модель возвращает текст до/после JSON — выдернем {...}
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        s = s[start:end + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _response_error_text(resp: httpx.Response, limit: int = 500) -> str:
    """Короткое тело ошибки провайдера без stacktrace и лишнего шума."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:limit]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("error") or err)[:limit]
        if isinstance(err, str):
            return err[:limit]
    return resp.text[:limit]


async def _call_chat(payload: dict, headers: dict, url: str, timeout: int) -> tuple[Optional[dict], Optional[str]]:
    """Один вызов LLM. Возвращает (parsed_json, error). При успехе error=None."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        return None, "LLM timeout"
    except httpx.HTTPError as exc:
        return None, f"LLM connection error: {exc}"

    if resp.status_code == 429:
        return None, "rate-limit"
    if resp.status_code in (401, 403):
        body = _response_error_text(resp)
        return None, f"LLM авторизация ({resp.status_code}): {body or 'проверьте LLM_API_KEY'}"
    if resp.status_code == 402:
        # OpenRouter возвращает 402 когда upstream-провайдер вернул insufficient_quota.
        # У тебя может быть $ на OpenRouter, но у конкретного провайдера квота кончилась.
        return None, (
            "У провайдера модели закончилась квота (HTTP 402). "
            "Смени LLM_MODEL на стабильную (например, deepseek/deepseek-chat-v3-0324) "
            "или перейди на прямой DeepSeek API (api.deepseek.com)."
        )
    if resp.status_code == 400:
        body = resp.text[:300]
        return None, f"bad-request: {body}"
    if resp.status_code >= 500:
        return None, f"LLM server {resp.status_code}"
    if resp.status_code != 200:
        return None, f"LLM HTTP {resp.status_code}: {resp.text[:200]}"

    try:
        data = resp.json()
        choice = data["choices"][0]
        msg = choice["message"]
        finish_reason = choice.get("finish_reason")
        # Reasoning-модели могут вернуть JSON в content или в reasoning_content
        content = msg.get("content") or msg.get("reasoning_content") or ""
    except (KeyError, IndexError, ValueError) as exc:
        return None, f"unexpected response shape: {exc}"

    parsed = _extract_json(content)
    if parsed is None:
        if finish_reason == "length":
            log.warning("LLM response truncated after %d chars", len(content))
            return None, "LLM response truncated (finish_reason=length)"
        log.warning("LLM вернул не-JSON (первые 300): %r", content[:300])
        return None, f"LLM вернул не-JSON: {content[:200]}"
    return parsed, None


async def chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    x_title: str = "DataMaps",
    model: Optional[str] = None,
    max_tokens: int = 900,
    fast: bool = False,
) -> tuple[Optional[dict], Optional[str]]:
    """Универсальный JSON-запрос к LLM. Возвращает (parsed_json, error_or_None).

    Логика:
    1. Пытается с response_format=json_object.
    2. На 400 — повторяет без response_format (некоторые модели его не принимают).
    3. На 429 (rate-limit) — ждёт 8 сек, ещё один ретрай.
    Каждая попытка чистит markdown и пытается выдернуть {...} из произвольного текста.
    """
    settings = get_settings()
    if not settings.llm_api_key:
        return None, "LLM_API_KEY не настроен"

    base_payload = {
        "model": model or settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if fast:
        base_payload.update({
            "reasoning": {"effort": "none", "exclude": True},
            "verbosity": "low",
        })
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.app_url,
        "X-Title": x_title,
        "User-Agent": "DataMaps/1.0 (+https://datamaps.ru)",
    }
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    request_timeout = min(settings.llm_timeout, 15) if fast else settings.llm_timeout

    payload = {**base_payload, "response_format": {"type": "json_object"}}
    parsed, err = await _call_chat(payload, headers, url, request_timeout)

    if parsed is None and err and err.startswith("bad-request"):
        log.info("LLM не принимает response_format, повтор без него")
        parsed, err = await _call_chat(base_payload, headers, url, request_timeout)

    if parsed is None and err and err.startswith("bad-request") and fast:
        log.info("LLM не принимает fast-параметры, повтор минимальным payload")
        plain_payload = {
            "model": model or settings.llm_model,
            "messages": base_payload["messages"],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        parsed, err = await _call_chat(plain_payload, headers, url, request_timeout)

    if parsed is None and err == "LLM timeout" and fast and model is None and settings.llm_model != "openai/gpt-4o-mini":
        fallback_payload = {**base_payload, "model": "openai/gpt-4o-mini", "response_format": {"type": "json_object"}}
        log.info("LLM fast timeout on %s, fallback to openai/gpt-4o-mini", settings.llm_model)
        parsed, err = await _call_chat(fallback_payload, headers, url, request_timeout)
        if parsed is None and err and err.startswith("bad-request"):
            fallback_payload.pop("response_format", None)
            parsed, err = await _call_chat(fallback_payload, headers, url, request_timeout)

    # До 3 ретраев на rate-limit с экспоненциальной задержкой: 6, 15, 30 сек
    delays = [6, 15, 30]
    attempt = 0
    while parsed is None and err == "rate-limit" and attempt < len(delays):
        wait = delays[attempt]
        log.info("LLM rate-limit, попытка %d/%d через %dс", attempt + 1, len(delays), wait)
        await asyncio.sleep(wait)
        parsed, err = await _call_chat(payload, headers, url, request_timeout)
        if parsed is None and err and err.startswith("bad-request"):
            parsed, err = await _call_chat(base_payload, headers, url, request_timeout)
        if parsed is None and err and err.startswith("bad-request") and fast:
            plain_payload = {
                "model": model or settings.llm_model,
                "messages": base_payload["messages"],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            parsed, err = await _call_chat(plain_payload, headers, url, request_timeout)
        attempt += 1

    if parsed is None:
        if err == "rate-limit":
            return None, "LLM временно перегружен (rate-limit). Попробуйте ещё раз через минуту."
        return None, err or "неизвестная ошибка LLM"
    return parsed, None


async def summarize(
    org_name: str,
    address: str,
    rating: Optional[float],
    reviews: list[dict],
) -> tuple[dict[str, Any], Optional[str]]:
    """Запрашивает у LLM JSON-сводку отзывов организации.

    Возвращает (summary, error_or_None). При ошибке summary — заглушка 'нет данных'.
    """
    if not reviews:
        return _empty(), None

    user_prompt = _build_user_prompt(org_name, address, rating, reviews)
    parsed, err = await chat_json(_SYSTEM_PROMPT, user_prompt, x_title="DataMaps Reviews")
    if parsed is None:
        log.warning("LLM summarize не удался: %s", err)
        return _empty(), err

    result = _empty()
    for k in result:
        if k in parsed:
            result[k] = parsed[k]
    return result, None
