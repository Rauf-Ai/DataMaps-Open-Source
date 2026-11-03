from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel

from .dedup import Deduplicator
from .manifest import ManifestBuilder


log = logging.getLogger(__name__)


def _media_descriptor(media) -> list[dict]:
    if not media:
        return []
    media_name = type(media).__name__.lower()
    if "photo" in media_name:
        return [{"type": "photo", "title": "Фотография"}]
    if "video" in media_name:
        return [{"type": "video", "title": "Видео"}]
    if "document" in media_name:
        return [{"type": "document", "title": "Документ"}]
    if "webpage" in media_name:
        webpage = getattr(media, "webpage", None)
        url = str(getattr(webpage, "url", "") or "")
        title = str(getattr(webpage, "title", "") or getattr(webpage, "display_url", "") or "Ссылка")
        return [{"type": "link", "title": title[:300], **({"url": url} if url.startswith(("http://", "https://")) else {})}]
    return [{"type": "attachment", "title": "Вложение"}]


def normalize_telegram_posts(messages, max_posts: int = 30) -> list[dict]:
    posts = []
    for message in list(messages or [])[:max_posts]:
        date = getattr(message, "date", None)
        posts.append({
            "id": int(getattr(message, "id", 0) or 0),
            "date": date.isoformat() if date else None,
            "text": str(getattr(message, "text", "") or getattr(message, "message", "") or "")[:3000],
            "views": int(getattr(message, "views", 0) or 0),
            "forwards": int(getattr(message, "forwards", 0) or 0),
            "media": _media_descriptor(getattr(message, "media", None)),
        })
    return posts


async def scrape_telegram(channel_username: str, work_dir: Path, dedup: Deduplicator, manifest: ManifestBuilder, max_messages: int = 30):
    """Collect a verified public Telegram broadcast channel without private data."""
    results = {"url": "", "kind": "channel", "title": "", "description": "", "subscribers": None, "posts": [], "errors": []}
    api_id, api_hash = os.environ.get("TELEGRAM_API_ID"), os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        results["errors"].append("TELEGRAM_API_ID или TELEGRAM_API_HASH не установлены")
        return results

    username = str(channel_username or "").rstrip("/").split("/")[-1].lstrip("@")
    client = TelegramClient(str(work_dir / f"tg_session_{username}"), int(api_id), api_hash)
    try:
        await client.connect()
        entity = await client.get_entity(username)
        if not isinstance(entity, Channel) or not entity.broadcast or not entity.username:
            results["errors"].append("Указанная ссылка не ведёт на публичный Telegram-канал")
            return results
        results.update({
            "url": f"https://t.me/{entity.username}",
            "title": str(entity.title or entity.username)[:500],
        })
        try:
            full = await client(GetFullChannelRequest(entity))
            results["description"] = str(getattr(full.full_chat, "about", "") or "")[:4000]
            results["subscribers"] = getattr(full.full_chat, "participants_count", None)
        except Exception as exc:  # Profile details may be unavailable while public posts remain readable.
            log.debug("Telegram channel profile unavailable for %s: %s", username, exc)
        results["posts"] = normalize_telegram_posts(await client.get_messages(entity, limit=min(max_messages, 30)), max_posts=30)
    except Exception as exc:
        log.warning("Telegram scraping failed for %s: %s", username, exc)
        results["errors"].append(str(exc)[:200])
    finally:
        await client.disconnect()

    file_path = work_dir / "telegram" / "data.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    content_bytes = json.dumps(results, ensure_ascii=False, indent=2).encode("utf-8")
    if dedup.should_download(results["url"] or f"tg://{username}", content_bytes) != "deduplicated":
        file_path.write_bytes(content_bytes)
        manifest.add_file(results["url"] or f"tg://{username}", str(file_path.relative_to(work_dir)), "application/json", len(content_bytes), dedup.compute_sha256(content_bytes), "telegram")
    return results
