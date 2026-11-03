from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Playwright-рендерер для JS-страниц Яндекс/2ГИС Карт (React SPA).

Витрина услуг, фото и акции подгружаются динамически и отсутствуют в статичном
HTML из requests. Два режима использования:
  1. fetch_rendered()  — one-shot, запускает/закрывает браузер на каждый вызов.
  2. BrowserSession    — переиспользуемая сессия: запустить один раз, fetch N страниц.

Запуск Chromium согласован с app.services.reviews_parser_browser (флаги --no-sandbox
и др. для работы в Docker-контейнере без привилегий).
"""

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Playwright при наличии SELENIUM_REMOTE_URL стартует Node-драйвер, который ходит
# в удалённый Selenium-grid вместо локального Chromium. Драйвер наследует env в
# момент sync_playwright(), поэтому переменную нужно убрать НА УРОВНЕ ИМПОРТА —
# до первого запуска Playwright в этом процессе. Иначе весь Playwright падает
# (парсинг отзывов, витрина/фото, Google-парсер).
os.environ.pop("SELENIUM_REMOTE_URL", None)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_CONTEXT_OPTS: dict = dict(
    user_agent=_UA,
    locale="ru-RU",
    ignore_https_errors=True,
    extra_http_headers={
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    },
)
# Флаги запуска для headless Chromium внутри Docker (как в reviews_parser_browser).
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]


def _find_chromium() -> str:
    candidates = [
        os.environ.get("CHROMIUM_PATH", ""),
        "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
        *[str(p) for p in sorted(
            Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux/chrome"),
            reverse=True,
        )],
        *[str(p) for p in sorted(
            Path.home().glob(
                "Library/Caches/ms-playwright/chromium-*/"
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium"
            ),
            reverse=True,
        )],
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return ""


def _launch_kwargs() -> dict:
    # Playwright при наличии SELENIUM_REMOTE_URL пытается ходить в удалённый
    # Selenium-grid вместо локального Chromium. На проде эта переменная осталась
    # в окружении и ломала ВЕСЬ Playwright (парсинг отзывов, витрина/фото).
    # Принудительно запускаем встроенный Chromium.
    os.environ.pop("SELENIUM_REMOTE_URL", None)
    kwargs: dict = {"headless": True, "args": _LAUNCH_ARGS}
    path = _find_chromium()
    if path:
        kwargs["executable_path"] = path
    return kwargs


def _is_real_page(html: str) -> bool:
    return len(html) >= 5_000


# ---------------------------------------------------------------------------
# One-shot helper (используется сборщиком лендинг-ZIP)
# ---------------------------------------------------------------------------
def fetch_rendered(
    url: str,
    wait_for_selector: str = "",
    scroll_px: int = 800,
    timeout_ms: int = 20_000,
    extra_wait_ms: int = 2_000,
) -> str:
    """Запустить Chromium, загрузить url, вернуть отрендеренный HTML или ''."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.debug("Playwright не установлен")
        return ""

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**_launch_kwargs())
            ctx = browser.new_context(**_CONTEXT_OPTS)
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PWTimeout:
                log.warning("fetch_rendered timeout: %s", url)
                browser.close()
                return ""

            if scroll_px:
                page.evaluate(f"window.scrollBy(0, {scroll_px})")
            if wait_for_selector:
                try:
                    page.wait_for_selector(wait_for_selector, timeout=5_000)
                except PWTimeout:
                    pass
            if extra_wait_ms:
                page.wait_for_timeout(extra_wait_ms)

            html = page.content()
            browser.close()
            if not _is_real_page(html):
                log.debug("fetch_rendered: tiny response (%d B) for %s", len(html), url)
                return ""
            return html
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch_rendered failed for %s: %s", url, exc)
        return ""


# ---------------------------------------------------------------------------
# Переиспользуемая сессия (используется живым поиском для bulk-обогащения)
# ---------------------------------------------------------------------------
class BrowserSession:
    """Один Chromium, живущий через множество fetch'ей.

    Запускается один раз в начале collect(), переиспользуется для каждой
    организации, затем stop(). Экономит ~2с launch-оверхеда на каждую страницу.

    Использование::

        with BrowserSession() as bs:
            html = bs.fetch("https://yandex.ru/maps/org/123/")
    """

    def __init__(self):
        self._pw = None
        self._browser = None
        self.available = False

    def start(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().__enter__()
            self._browser = self._pw.chromium.launch(**_launch_kwargs())
            self.available = True
            log.debug("BrowserSession запущена")
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("BrowserSession недоступна: %s", exc)
            self.available = False
            return False

    def stop(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        self.available = False
        self._browser = None
        self._pw = None

    def fetch(
        self,
        url: str,
        scroll_px: int = 400,
        wait_ms: int = 1_500,
        timeout_ms: int = 15_000,
    ) -> str:
        """Загрузить url в свежем контексте, вернуть HTML или ''."""
        if not self.available or not self._browser:
            return ""
        try:
            from playwright.sync_api import TimeoutError as PWTimeout
            ctx = self._browser.new_context(**_CONTEXT_OPTS)
            page = ctx.new_page()
            loaded = False
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                loaded = True
            except PWTimeout:
                try:
                    loaded = page.url not in ("", "about:blank")
                except Exception:  # noqa: BLE001
                    pass
            if not loaded:
                ctx.close()
                return ""
            if scroll_px:
                try:
                    page.evaluate(f"window.scrollBy(0, {scroll_px})")
                except Exception:  # noqa: BLE001
                    pass
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            try:
                html = page.content()
            except Exception:  # noqa: BLE001
                ctx.close()
                return ""
            ctx.close()
            if not _is_real_page(html):
                return ""
            return html
        except Exception as exc:  # noqa: BLE001
            log.debug("BrowserSession.fetch %s: %s", url, exc)
            return ""

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
