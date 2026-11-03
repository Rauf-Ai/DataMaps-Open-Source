from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Глубокий парсер отзывов через Playwright (headless Chromium).

Открывает /maps/org/<id>/reviews/, скроллит список до тех пор, пока подгружаются
новые отзывы (AJAX), и затем парсит DOM теми же экстракторами, что и HTTP-режим.
Это позволяет получить >50 отзывов (HTTP-fallback видит только пре-рендеренные).
"""

import logging
import os

from app.services.reviews_parser import is_captcha_html, parse_dom


log = logging.getLogger(__name__)


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_SCROLL_JS = """
() => {
  // Скроллим контейнер списка отзывов (если есть) или окно
  const candidates = [
    document.querySelector('.business-reviews-card-view__reviews-container'),
    document.querySelector('[class*="reviews-card-view__reviews"]'),
    document.querySelector('.scroll__container'),
  ].filter(Boolean);
  for (const el of candidates) {
    el.scrollTop = el.scrollHeight;
  }
  window.scrollTo(0, document.body.scrollHeight);
  return document.querySelectorAll('.business-review-view').length;
}
"""


async def fetch_reviews_deep(
    org_id: str,
    *,
    stars_min: int = 1,
    stars_max: int = 5,
    max_reviews: int = 200,
    max_scrolls: int = 60,
) -> dict:
    """Парсит страницу отзывов через Playwright со скроллом DOM.

    Возвращает {company_info, company_reviews} либо {error: "..."}.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"error": "playwright не установлен в backend-контейнере"}

    url = f"https://yandex.ru/maps/org/{org_id}/reviews/"

    # Игнорируем SELENIUM_REMOTE_URL из окружения — запускаем локальный Chromium.
    os.environ.pop("SELENIUM_REMOTE_URL", None)
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Playwright launch failed: %s", exc)
            return {"error": f"Не удалось запустить браузер: {exc}"}

        try:
            ctx = await browser.new_context(
                user_agent=_UA,
                locale="ru-RU",
                viewport={"width": 1366, "height": 900},
                extra_http_headers={
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                },
            )
            page = await ctx.new_page()
            page.set_default_timeout(60_000)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:  # noqa: BLE001
                return {"error": f"Не удалось открыть страницу: {exc}"}

            html_initial = await page.content()
            if is_captcha_html(html_initial):
                return {"error": "Яндекс показал капчу (deep)"}

            # Дать React-у дорендерить первый экран
            try:
                await page.wait_for_selector(".business-review-view", timeout=15_000)
            except Exception:  # noqa: BLE001
                # Возможно, у организации нет отзывов или другая структура — продолжим попытку
                pass

            # Скролл до стабильности или лимита
            prev_count = 0
            stable_iters = 0
            for _ in range(max_scrolls):
                try:
                    count = await page.evaluate(_SCROLL_JS)
                except Exception:  # noqa: BLE001
                    break
                if not isinstance(count, int):
                    break
                if count >= max_reviews:
                    break
                if count == prev_count:
                    stable_iters += 1
                    if stable_iters >= 3:
                        break
                else:
                    stable_iters = 0
                prev_count = count
                await page.wait_for_timeout(900)

            html = await page.content()
        finally:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass

    if is_captcha_html(html):
        return {"error": "Яндекс показал капчу (после скролла)"}

    return parse_dom(html, stars_min=stars_min, stars_max=stars_max)
