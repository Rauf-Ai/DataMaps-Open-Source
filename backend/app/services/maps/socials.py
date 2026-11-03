"""Shared validation and classification of public social-network profiles."""

from __future__ import annotations

from html import unescape
import re
from urllib.parse import unquote, urlparse


NETWORK_DOMAINS: dict[str, tuple[str, ...]] = {
    "vk": ("vk.com", "vk.ru", "vkontakte.ru"),
    "max": ("max.ru",),
    "instagram": ("instagram.com",),
    "telegram": ("t.me", "telegram.me", "telegram.org"),
    "ok": ("ok.ru",),
    "facebook": ("facebook.com", "fb.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "tiktok": ("tiktok.com",),
    "twitter": ("twitter.com", "x.com"),
    "whatsapp": ("whatsapp.com", "wa.me"),
    "linkedin": ("linkedin.com",),
}

_DOMAIN_TO_NETWORK = {
    domain: network
    for network, domains in NETWORK_DOMAINS.items()
    for domain in domains
}
_SOCIAL_DOMAIN_PATTERN = "|".join(
    re.escape(domain) for domain in sorted(_DOMAIN_TO_NETWORK, key=len, reverse=True)
)
_SOCIAL_URL_RE = re.compile(
    rf"(?<![A-Za-z0-9._/\\-])(?:(?:https?:)?//)?(?:www\\.)?(?:{_SOCIAL_DOMAIN_PATTERN})(?:/[^\\s<>'\"`,;]*)?",
    re.IGNORECASE,
)


def _network_for_host(host: str) -> str | None:
    host = host.casefold().rstrip(".")
    for domain, network in _DOMAIN_TO_NETWORK.items():
        if host == domain or host.endswith(f".{domain}"):
            return network
    return None


def _is_yandex_navigation_profile(path: str) -> bool:
    first_segment = unquote(path).strip("/").casefold().split("/", 1)[0]
    return first_segment.startswith("yandex") or first_segment == "mapsyandex"


def normalize_social_url(url: str) -> str | None:
    """Return a canonical public social-profile URL, or ``None`` if invalid."""
    raw = unescape(str(url or "")).strip().replace("\\/", "/")
    raw = raw.strip("'\"<>()[]{} ").rstrip(".,;:!?")
    if not raw or raw == "—":
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    elif not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    if parsed.scheme.casefold() not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not _network_for_host(host):
        return None
    path = parsed.path or ""
    if not path.strip("/") or _is_yandex_navigation_profile(path):
        return None

    return parsed._replace(
        scheme="https",
        netloc=host,
        fragment="",
    ).geturl()


def classify_social_url(url: str) -> str | None:
    """Return the registry key for a valid profile URL."""
    normalized = normalize_social_url(url)
    if not normalized:
        return None
    return _network_for_host(urlparse(normalized).hostname or "")


def extract_social_urls(text: str) -> list[str]:
    """Extract unique, validated social-profile links from text or HTML."""
    raw = unescape(str(text or "")).replace("\\/", "/")
    candidates = [raw, *[match.group(0) for match in _SOCIAL_URL_RE.finditer(raw)]]
    urls: list[str] = []
    for candidate in candidates:
        normalized = normalize_social_url(candidate)
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def has_social_network(value: str, network: str) -> bool:
    """Check social text by parsed host, never by a substring match."""
    if network not in NETWORK_DOMAINS:
        return False
    return any(classify_social_url(url) == network for url in extract_social_urls(value))
