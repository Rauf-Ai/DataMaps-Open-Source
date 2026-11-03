from __future__ import annotations

import json
import logging
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from trafilatura import extract

from .dedup import Deduplicator
from .manifest import ManifestBuilder
from .security import validate_url


log = logging.getLogger(__name__)
_PAGE_HINTS = ("service", "услуг", "price", "цен", "contact", "контакт", "about", "о-компан", "delivery", "достав", "booking", "запис")


def _canonical_url(url: str) -> str:
    url, _fragment = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path.rstrip("/") or "/", "", parsed.query, ""))


def prioritize_site_urls(start_url: str, links, max_pages: int = 5) -> list[str]:
    start = _canonical_url(start_url)
    origin = (urlparse(start).scheme, urlparse(start).hostname, urlparse(start).port)
    candidates = []
    seen = {start}
    for href, label in links or []:
        absolute = _canonical_url(urljoin(start, str(href or "")))
        parsed = urlparse(absolute)
        if (parsed.scheme, parsed.hostname, parsed.port) != origin or absolute in seen:
            continue
        seen.add(absolute)
        text = f"{parsed.path} {label or ''}".lower()
        hint = next((index for index, token in enumerate(_PAGE_HINTS) if token in text), len(_PAGE_HINTS))
        candidates.append((hint, len(candidates), absolute))
    return [start] + [url for _hint, _index, url in sorted(candidates)[: max(0, max_pages - 1)]]


def _page_content(url: str, html: str, title: str = "") -> dict:
    data = {}
    extracted = extract(html, include_comments=False, include_tables=True, output_format="json")
    if extracted:
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError:
            data = {}
    return {
        "url": url,
        "title": str(data.get("title") or title or "")[:500],
        "description": str(data.get("description") or "")[:1000],
        "text": str(data.get("text") or "")[:10000],
    }


async def scrape_website(url: str, work_dir, dedup: Deduplicator, manifest: ManifestBuilder, max_pages: int = 5):
    """Collect up to five readable pages from one public website origin."""
    start_url = _canonical_url(validate_url(url))
    results = {"url": start_url, "pages": [], "errors": []}
    origin = (urlparse(start_url).scheme, urlparse(start_url).hostname, urlparse(start_url).port)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="DataMaps/1.0 (Analysis Bot)")
            discovered_links = []
            try:
                first_page = await context.new_page()
                await first_page.goto(start_url, wait_until="domcontentloaded", timeout=15_000)
                if (urlparse(first_page.url).scheme, urlparse(first_page.url).hostname, urlparse(first_page.url).port) != origin:
                    raise ValueError("Сайт перенаправил на другой домен")
                first_html = await first_page.content()
                results["pages"].append(_page_content(start_url, first_html, await first_page.title()))
                discovered_links = await first_page.eval_on_selector_all(
                    "a[href]", "elements => elements.map(element => [element.href, element.textContent])"
                )
                await first_page.close()
                for page_url in prioritize_site_urls(start_url, discovered_links, max_pages=max_pages)[1:]:
                    try:
                        validate_url(page_url)
                        page = await context.new_page()
                        await page.goto(page_url, wait_until="domcontentloaded", timeout=15_000)
                        current = _canonical_url(page.url)
                        if (urlparse(current).scheme, urlparse(current).hostname, urlparse(current).port) != origin:
                            raise ValueError("Страница перенаправила на другой домен")
                        results["pages"].append(_page_content(current, await page.content(), await page.title()))
                        await page.close()
                    except PlaywrightTimeout:
                        results["errors"].append(f"Таймаут страницы: {page_url}")
                    except Exception as exc:
                        results["errors"].append(f"Страница не собрана: {str(exc)[:160]}")
            except PlaywrightTimeout:
                results["errors"].append("Таймаут загрузки сайта")
            finally:
                await context.close()
                await browser.close()
    except Exception as exc:
        log.warning("Website scraping failed for %s: %s", start_url, exc)
        results["errors"].append(str(exc)[:200])

    if results["pages"]:
        first = results["pages"][0]
        results.update({"title": first["title"], "description": first["description"], "text": first["text"]})
    file_path = work_dir / "website" / "content.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    content_bytes = json.dumps(results, ensure_ascii=False, indent=2).encode("utf-8")
    if dedup.should_download(start_url, content_bytes) != "deduplicated":
        file_path.write_bytes(content_bytes)
        manifest.add_file(start_url, str(file_path.relative_to(work_dir)), "application/json", len(content_bytes), dedup.compute_sha256(content_bytes), "website")
    return results
