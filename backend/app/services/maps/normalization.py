from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Small, provider-independent normalizers for live map rows."""

import re
from urllib.parse import parse_qs, unquote, urlparse


_INTERNAL_HOSTS = {"datamaps.ru", "www.datamaps.ru"}
_REDIRECT_QUERY_KEYS = ("url", "target", "redirect", "redirect_url", "continue", "to")
_NUMBER = r"(?:\d{1,3}(?:[\s\u00a0,.]\d{3})+|\d+)"
_COUNT_PATTERN = re.compile(
    rf"({_NUMBER})(?=\s*(?:отзыв|review|ulasan|评价|評價|recension|bewertung))",
    re.IGNORECASE,
)
_LOCALES = {
    "сша": ("en", "en-US"),
    "usa": ("en", "en-US"),
    "united states": ("en", "en-US"),
    "индонезия": ("id", "id-ID"),
    "indonesia": ("id", "id-ID"),
    "китай": ("zh-CN", "zh-CN"),
    "china": ("zh-CN", "zh-CN"),
    "россия": ("ru", "ru-RU"),
    "russia": ("ru", "ru-RU"),
    "германия": ("de", "de-DE"),
    "germany": ("de", "de-DE"),
    "франция": ("fr", "fr-FR"),
    "france": ("fr", "fr-FR"),
    "япония": ("ja", "ja-JP"),
    "japan": ("ja", "ja-JP"),
    "испания": ("es", "es-ES"),
    "spain": ("es", "es-ES"),
    "италия": ("it", "it-IT"),
    "italy": ("it", "it-IT"),
    "бразилия": ("pt-BR", "pt-BR"),
    "brazil": ("pt-BR", "pt-BR"),
    "турция": ("tr", "tr-TR"),
    "turkey": ("tr", "tr-TR"),
}


def _unwrap_redirect(url: str) -> str:
    """Return the external target carried by a common maps redirect URL."""
    candidate = url
    for _ in range(2):
        parsed = urlparse(candidate)
        values = parse_qs(parsed.query)
        target = next((values[key][0] for key in _REDIRECT_QUERY_KEYS if values.get(key)), "")
        if not target:
            break
        candidate = unquote(target).strip()
    return candidate


def absolute_external_url(value: str) -> str:
    """Normalize a provider website to a safe absolute URL, or return ``''``."""
    raw = str(value or "").strip()
    if not raw or raw == "—":
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        raw = f"https://{raw}"
    raw = _unwrap_redirect(raw)
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    host = (parsed.hostname or "").lower()
    if host in _INTERNAL_HOSTS or host.endswith(".datamaps.ru"):
        return ""
    return parsed._replace(scheme=parsed.scheme.lower(), fragment="").geturl()


def parse_count(value: str) -> int:
    """Extract an integer count from a localized map label without parsing rating."""
    text = str(value or "")
    match = _COUNT_PATTERN.search(text)
    if not match:
        match = re.search(rf"\(({_NUMBER})\)", text)
    if not match:
        match = re.search(rf"\b({_NUMBER})\b", text)
    if not match:
        return 0
    digits = re.sub(r"\D", "", match.group(1))
    try:
        return int(digits) if digits else 0
    except ValueError:
        return 0


def locale_for_country(country: str) -> tuple[str, str]:
    """Return search language and Google Maps locale for a country name."""
    normalized = " ".join(str(country or "").casefold().replace("ё", "е").split())
    return _LOCALES.get(normalized, ("en", "en-US"))
