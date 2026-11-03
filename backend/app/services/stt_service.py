from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import base64

import httpx

from app.config import get_settings


settings = get_settings()

_FORMAT_BY_CONTENT_TYPE = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/flac": "flac",
}


def audio_format(content_type: str) -> str:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    try:
        return _FORMAT_BY_CONTENT_TYPE[normalized]
    except KeyError as exc:
        raise ValueError("Поддерживаются WebM, OGG, WAV, MP3, M4A, AAC и FLAC") from exc


async def transcribe_audio(data: bytes, content_type: str, *, client: httpx.Optional[AsyncClient] = None) -> str:
    if not settings.llm_api_key:
        raise RuntimeError("[STT-201] OpenRouter API key не настроен")
    if not data:
        raise ValueError("Аудиозапись пустая")
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Аудиозапись превышает 10 МБ")

    payload = {
        "model": settings.stt_model,
        "input_audio": {
            "data": base64.b64encode(data).decode("ascii"),
            "format": audio_format(content_type),
        },
        "language": "ru",
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=max(30, min(60, settings.llm_timeout)))
    try:
        response = await client.post(
            f"{settings.llm_base_url.rstrip('/')}/audio/transcriptions",
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": settings.app_url,
                "X-Title": "DataMaps voice search",
            },
            json=payload,
        )
        if response.status_code >= 400:
            detail = response.text[:600]
            raise RuntimeError(f"[STT-{response.status_code}] OpenRouter transcription: {detail}")
        body = response.json()
        text = str(body.get("text") or "").strip()
        if not text:
            raise RuntimeError("[STT-204] Модель не распознала речь")
        return text
    except httpx.TimeoutException as exc:
        raise RuntimeError("[STT-205] Распознавание речи превысило время ожидания") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"[STT-206] OpenRouter недоступен: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()
