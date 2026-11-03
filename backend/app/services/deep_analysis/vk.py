from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import vk_api

from .dedup import Deduplicator
from .manifest import ManifestBuilder


log = logging.getLogger(__name__)


def _largest_photo_url(photo: dict) -> str:
    sizes = photo.get("sizes") or []
    largest = max(sizes, key=lambda item: (int(item.get("width") or 0) * int(item.get("height") or 0), int(item.get("width") or 0)), default={})
    return str(largest.get("url") or "")


def _vk_media(attachments) -> list[dict]:
    media = []
    for attachment in attachments or []:
        kind = str(attachment.get("type") or "attachment")
        payload = attachment.get(kind) or {}
        if kind == "photo":
            url = _largest_photo_url(payload)
            media.append({"type": "photo", "title": "Фотография", **({"url": url} if url.startswith(("http://", "https://")) else {})})
        elif kind == "video":
            owner_id, video_id = payload.get("owner_id"), payload.get("id")
            url = f"https://vk.com/video{owner_id}_{video_id}" if owner_id is not None and video_id is not None else ""
            media.append({"type": "video", "title": str(payload.get("title") or "Видео")[:300], **({"url": url} if url else {})})
        else:
            url = str(payload.get("url") or payload.get("link_url") or "")
            media.append({"type": kind, "title": str(payload.get("title") or payload.get("name") or "Вложение")[:300], **({"url": url} if url.startswith(("http://", "https://")) else {})})
    return media


def normalize_vk_posts(items, max_posts: int = 30) -> list[dict]:
    posts = []
    for item in list(items or [])[:max_posts]:
        timestamp = item.get("date")
        date = datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if isinstance(timestamp, (int, float)) else None
        posts.append({
            "id": int(item.get("id") or 0),
            "date": date,
            "text": str(item.get("text") or "")[:3000],
            "likes": int((item.get("likes") or {}).get("count") or 0),
            "comments": int((item.get("comments") or {}).get("count") or 0),
            "reposts": int((item.get("reposts") or {}).get("count") or 0),
            "media": _vk_media(item.get("attachments")),
        })
    return posts


def scrape_vk(group_id: str, work_dir: Path, dedup: Deduplicator, manifest: ManifestBuilder, max_posts: int = 30):
    """Collect a verified VK community and normalize public wall posts."""
    results = {"url": "", "kind": "community", "title": "", "description": "", "members": None, "posts": [], "errors": []}
    token = os.environ.get("VK_API_TOKEN")
    if not token:
        results["errors"].append("VK_API_TOKEN не установлен в окружении")
        return results

    screen_name = str(group_id or "").rstrip("/").split("/")[-1]
    try:
        api = vk_api.VkApi(token=token).get_api()
        resolved = api.utils.resolveScreenName(screen_name=screen_name) or {}
        if str(resolved.get("object_type") or resolved.get("type") or "") not in {"group", "page", "event"}:
            results["errors"].append("Указанная ссылка не ведёт на публичное сообщество VK")
            return results
        numeric_id = int(resolved.get("object_id") or resolved.get("id"))
        group = (api.groups.getById(group_id=numeric_id, fields="description,members_count,screen_name,type") or [{}])[0]
        canonical = str(group.get("screen_name") or screen_name)
        results.update({
            "url": f"https://vk.com/{canonical}",
            "title": str(group.get("name") or canonical)[:500],
            "description": str(group.get("description") or "")[:4000],
            "members": group.get("members_count"),
            "kind": str(group.get("type") or "community"),
        })
        wall = api.wall.get(owner_id=-numeric_id, count=min(max_posts, 30), filter="owner") or {}
        results["posts"] = normalize_vk_posts(wall.get("items"), max_posts=30)
    except Exception as exc:
        log.warning("VK scraping failed for %s: %s", screen_name, exc)
        results["errors"].append(str(exc)[:200])

    file_path = work_dir / "vk" / "data.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    content_bytes = json.dumps(results, ensure_ascii=False, indent=2).encode("utf-8")
    if dedup.should_download(results["url"] or f"vk://{screen_name}", content_bytes) != "deduplicated":
        file_path.write_bytes(content_bytes)
        manifest.add_file(results["url"] or f"vk://{screen_name}", str(file_path.relative_to(work_dir)), "application/json", len(content_bytes), dedup.compute_sha256(content_bytes), "vk")
    return results
