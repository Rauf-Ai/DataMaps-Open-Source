from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import urlparse, urlunparse

from telethon import TelegramClient
from telethon.tl.types import Channel
import vk_api


_CHANNEL_KEYS = ("website", "telegram", "vk")
_LABELS = {"website": "Сайт", "telegram": "Telegram", "vk": "ВКонтакте"}
_SOCIAL_URL_RE = re.compile(
    r"(?:(?:https?://)?(?:t\.me|telegram\.me|vk\.com|vkontakte\.ru)/[^\s,;]+)",
    re.IGNORECASE,
)
_TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,}$")


def _record(business_id: int | None, key: str, url: str = "", status: str = "missing", reason: str = "", **extra) -> dict:
    record = {
        "business_id": business_id,
        "key": key,
        "label": _LABELS[key],
        "url": url,
        "status": status,
        "reason": reason,
    }
    record.update(extra)
    return record


def normalize_public_url(raw: object) -> str:
    value = str(raw or "").strip().strip(".,;)")
    if not value:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
        value = f"https://{value.lstrip('/')}"
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password or parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        return ""
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", parsed.query, ""))


def _social_urls(raw: object) -> list[str]:
    value = str(raw or "")
    return [normalize_public_url(match.group(0)) for match in _SOCIAL_URL_RE.finditer(value) if normalize_public_url(match.group(0))]


def _telegram_username(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    first = path.split("/", 1)[0]
    if parsed.hostname not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        return "", "invalid"
    if first.startswith("+") or first.lower() in {"joinchat", "share", "resolve", "proxy", "socks", "addstickers"}:
        return "", "private"
    if not _TELEGRAM_USERNAME_RE.fullmatch(first):
        return "", "invalid"
    return first, ""


def _vk_screen_name(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    first = path.split("/", 1)[0]
    if parsed.hostname not in {"vk.com", "www.vk.com", "vkontakte.ru", "www.vkontakte.ru"}:
        return "", "invalid"
    if first.lower() in {"im", "mail", "feed", "away", "login", "settings", "support"} or first.lower().startswith("id"):
        return "", "personal"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,100}", first):
        return "", "invalid"
    return first, ""


async def resolve_telegram_url(url: str) -> dict:
    username, invalid_status = _telegram_username(url)
    if invalid_status == "private":
        return {"url": url, "status": "private", "reason": "Личный или закрытый Telegram, не анализируется"}
    if invalid_status:
        return {"url": url, "status": "invalid", "reason": "Некорректная ссылка Telegram"}
    api_id, api_hash = os.environ.get("TELEGRAM_API_ID"), os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        return {"url": url, "status": "unsupported", "reason": "Проверка Telegram недоступна: не настроен API"}

    client = TelegramClient(f"tg_discovery_{username}", int(api_id), api_hash)
    try:
        await client.connect()
        entity = await client.get_entity(username)
        if isinstance(entity, Channel) and bool(getattr(entity, "broadcast", False)) and getattr(entity, "username", None):
            return {"url": f"https://t.me/{entity.username}", "status": "available", "reason": ""}
        return {"url": url, "status": "personal", "reason": "Личный Telegram, не анализируется"}
    except Exception:
        return {"url": url, "status": "private", "reason": "Личный или закрытый Telegram, не анализируется"}
    finally:
        await client.disconnect()


def _resolve_vk_url_sync(url: str) -> dict:
    screen_name, invalid_status = _vk_screen_name(url)
    if invalid_status == "personal":
        return {"url": url, "status": "personal", "reason": "Личный профиль VK, не анализируется"}
    if invalid_status:
        return {"url": url, "status": "invalid", "reason": "Некорректная ссылка VK"}
    token = os.environ.get("VK_API_TOKEN")
    if not token:
        return {"url": url, "status": "unsupported", "reason": "Проверка VK недоступна: не настроен API"}
    try:
        api = vk_api.VkApi(token=token).get_api()
        resolved = api.utils.resolveScreenName(screen_name=screen_name) or {}
        object_type = str(resolved.get("object_type") or resolved.get("type") or "")
        if object_type not in {"group", "page", "event"}:
            return {"url": url, "status": "personal", "reason": "Личный профиль VK, не анализируется"}
        group_id = resolved.get("object_id") or resolved.get("id")
        if not group_id:
            return {"url": url, "status": "invalid", "reason": "Не удалось определить сообщество VK"}
        group = (api.groups.getById(group_id=group_id, fields="description") or [{}])[0]
        canonical = group.get("screen_name") or screen_name
        return {"url": f"https://vk.com/{canonical}", "status": "available", "reason": "", "group_id": int(group_id)}
    except Exception:
        return {"url": url, "status": "private", "reason": "Сообщество VK недоступно для анализа"}


async def resolve_vk_url(url: str) -> dict:
    return await asyncio.to_thread(_resolve_vk_url_sync, url)


async def discover_company_channels(target: dict) -> dict[str, list[dict]]:
    business_id = target.get("business_id")
    result = {key: [] for key in _CHANNEL_KEYS}
    website = normalize_public_url(target.get("website") or target.get("site"))
    result["website"].append(_record(
        business_id, "website", website,
        "available" if website else "missing",
        "" if website else "Сайт не указан в карточке организации",
    ))

    social_urls = _social_urls(target.get("social"))
    telegram_urls = [url for url in social_urls if urlparse(url).hostname in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}]
    vk_urls = [url for url in social_urls if urlparse(url).hostname in {"vk.com", "www.vk.com", "vkontakte.ru", "www.vkontakte.ru"}]
    if telegram_urls:
        eligible_urls, rejected = [], []
        for url in telegram_urls:
            _username, invalid_status = _telegram_username(url)
            if invalid_status == "private":
                rejected.append({"url": url, "status": "private", "reason": "Личный или закрытый Telegram, не анализируется"})
            elif invalid_status:
                rejected.append({"url": url, "status": "invalid", "reason": "Некорректная ссылка Telegram"})
            else:
                eligible_urls.append(url)
        for resolved in rejected + list(await asyncio.gather(*(resolve_telegram_url(url) for url in eligible_urls))):
            result["telegram"].append(_record(business_id, "telegram", **resolved))
    else:
        result["telegram"].append(_record(business_id, "telegram", reason="Telegram не указан в карточке организации"))
    if vk_urls:
        eligible_urls, rejected = [], []
        for url in vk_urls:
            _screen_name, invalid_status = _vk_screen_name(url)
            if invalid_status == "personal":
                rejected.append({"url": url, "status": "personal", "reason": "Личный профиль VK, не анализируется"})
            elif invalid_status:
                rejected.append({"url": url, "status": "invalid", "reason": "Некорректная ссылка VK"})
            else:
                eligible_urls.append(url)
        for resolved in rejected + list(await asyncio.gather(*(resolve_vk_url(url) for url in eligible_urls))):
            result["vk"].append(_record(business_id, "vk", **resolved))
    else:
        result["vk"].append(_record(business_id, "vk", reason="ВКонтакте не указан в карточке организации"))
    return result


def available_channel_ids(discovery: dict[str, list[dict]]) -> dict[str, list[int]]:
    return {
        key: [int(row["business_id"]) for row in discovery.get(key, []) if row.get("status") == "available" and row.get("business_id") is not None]
        for key in _CHANNEL_KEYS
    }
