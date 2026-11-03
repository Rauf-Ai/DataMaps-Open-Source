from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Парсер Google Карт через Playwright (третий источник живого поиска).

Подход и селекторы адаптированы из kevmaindev/Googles-Maps-Scraper под контракт
DataMaps (та же форма строки, что у yandex_search/twogis_search). У Google нет
бесплатного API — работает автоматизация веб-UI headless-Chromium.

Особенности:
  * Google капит ~120 результатов на запрос;
  * селекторы обфусцированы и меняются — везде фолбэки и мягкая деградация,
    collect() никогда не бросает исключение наружу (пустые поля → сентинелы);
  * `social` обычно "—" (Google не отдаёт соцсети);
  * `oid` — ftid (0x…:0x…) из URL места, иначе хэш name+address.
"""

import hashlib
import logging
import re
from urllib.parse import quote

from app.config import get_settings
from app.services.maps.browser import _CONTEXT_OPTS, _launch_kwargs
from app.services.maps.normalization import absolute_external_url, locale_for_country, parse_count

log = logging.getLogger(__name__)
settings = get_settings()

_CONSENT_COOKIES = [
    {"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"},
    {"name": "SOCS", "value": "CAI", "domain": ".google.com", "path": "/"},
]
_PLACE_LINK = 'a[href*="/maps/place/"]'
_GOOGLE_CAP = 120  # Google не отдаёт больше ~120 результатов на запрос


# ── низкоуровневые помощники по странице ───────────────────────────────────
def _text(page, sel: str) -> str:
    try:
        el = page.query_selector(sel)
        return (el.inner_text() or "").strip() if el else ""
    except Exception:  # noqa: BLE001
        return ""


def _attr(page, sel: str, attr: str) -> str:
    try:
        el = page.query_selector(sel)
        return (el.get_attribute(attr) or "").strip() if el else ""
    except Exception:  # noqa: BLE001
        return ""


def _clean_site(url: str) -> str:
    url = re.sub(r"^https?://", "", url.strip())
    return url.rstrip("/")


def _review_count_from_label(label: str) -> int:
    """Parse a Google Maps review total independently of the display language."""
    return parse_count(label)


def _google_maps_search_url(query: str, country: str = "") -> str:
    language, _locale = locale_for_country(country)
    return f"https://www.google.com/maps/search/{quote(query)}/?hl={quote(language)}"


def _coords(url: str):
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url or "")
    if m:
        try:
            return round(float(m.group(1)), 6), round(float(m.group(2)), 6)
        except (TypeError, ValueError):
            pass
    return "—", "—"


def _ftid(url: str) -> str:
    m = re.search(r"(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)", url or "")
    return m.group(1) if m else ""


def _rating_reviews(page):
    rating = "—"
    reviews = 0
    for sel in ('div.F7nice span[aria-hidden="true"]', "div.fontDisplayLarge",
                'div[jsaction*="reviewChart"] div[role="img"]'):
        raw = _text(page, sel) or _attr(page, sel, "aria-label")
        m = re.search(r"(\d+[.,]\d+)", raw)
        if m:
            try:
                rating = round(float(m.group(1).replace(",", ".")), 1)
                break
            except (TypeError, ValueError):
                continue
    for sel in ('div.F7nice span[aria-label]', 'button[jsaction*="reviewChart"]',
                'div.F7nice'):
        raw = _text(page, sel) or _attr(page, sel, "aria-label")
        reviews = _review_count_from_label(raw)
        if reviews:
            break
    return rating, reviews


def _price(page) -> str:
    for sel in ('span[aria-label*="Цен"]', 'span[aria-label*="Price"]',
                'span[aria-label*="₽"]', 'span[aria-label*="$"]'):
        v = _attr(page, sel, "aria-label")
        if v:
            return v[:22]
    return ""


def _accept_consent(page) -> None:
    for sel in ('button[aria-label*="Accept all"]', 'button[aria-label*="Принять все"]',
                'form[action*="consent"] button', 'button:has-text("Принять все")',
                'button:has-text("Accept all")'):
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.click(timeout=3_000)
                page.wait_for_timeout(2_000)
                return
        except Exception:  # noqa: BLE001
            continue


# ── извлечение одной карточки места ────────────────────────────────────────
def _extract(page, href: str) -> Optional[dict]:
    name = _text(page, "h1.DUwDvf") or _text(page, "h1")
    if not name:
        return None

    address = (_text(page, 'button[data-item-id="address"] div.fontBodyMedium')
               or _attr(page, 'button[data-item-id="address"]', "aria-label").replace("Адрес:", "").strip()
               or "—")

    site = "—"
    has_site = "Нет"
    dom = (_text(page, 'a[data-item-id="authority"] div.fontBodyMedium')
           or _attr(page, 'a[data-item-id="authority"]', "aria-label"))
    dom = re.sub(r"^(Веб-сайт|Website|Сайт):?\s*", "", dom).strip()
    if dom and "." in dom:
        site = absolute_external_url(dom) or "—"
        has_site = "Да" if site != "—" else "Нет"

    phone = "—"
    raw_phone = _attr(page, 'button[data-item-id^="phone:tel:"]', "data-item-id")
    if raw_phone:
        phone = raw_phone.split("phone:tel:")[-1].strip() or "—"
    else:
        t = _text(page, 'button[data-item-id^="phone:tel:"] div.fontBodyMedium')
        if t:
            phone = t.strip()

    category = _text(page, "button.DkEaL") or _text(page, 'button[jsaction*="category"]') or "—"

    rating, reviews = _rating_reviews(page)
    price = _price(page)
    lat, lon = _coords(page.url)
    if lat == "—":
        lat, lon = _coords(href)

    oid = _ftid(href) or _ftid(page.url)
    if not oid:
        oid = "g" + hashlib.md5(f"{name}|{address}".encode("utf-8")).hexdigest()[:16]

    return {
        "oid": oid,
        "name": name,
        "phone": phone,
        "site": site,
        "social": "—",
        "address": address,
        "category": category,
        "rating": rating,
        "reviews": reviews,
        "lat": lat,
        "lon": lon,
        "has_site": has_site,
        "hours": "",
        "services": "",
        "features": "",
        "price_range": price,
        "map_url": href or page.url,
    }


# ── главная collect() ──────────────────────────────────────────────────────
def collect(query: str, max_companies: int = 100, emit=None, filter_fn=None, country: str = "") -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.warning("google_search: Playwright не установлен")
        return []

    def _emit(msg: str, found: int, *, checked: int = 0, organization: Optional[dict] = None):
        if emit:
            emit(
                "match" if organization else "progress",
                found=found,
                total=max_companies,
                checked=checked,
                organization=organization,
                message=msg,
            )

    target = max(1, min(max_companies, _GOOGLE_CAP))
    # При фильтрах просматриваем всю доступную Google ленту: небольшой target
    # не должен завершать поиск после первых 20 карточек, если они не подошли.
    hard_cap = _GOOGLE_CAP if filter_fn else target

    all_rows: list[dict] = []
    passed: list[dict] = []
    seen: set[tuple] = set()

    launch_kwargs = _launch_kwargs()
    if settings.google_proxy:
        launch_kwargs["proxy"] = {"server": settings.google_proxy}
    language, google_locale = locale_for_country(country)
    context_options = {
        **_CONTEXT_OPTS,
        "locale": google_locale,
        "extra_http_headers": {
            **_CONTEXT_OPTS.get("extra_http_headers", {}),
            "Accept-Language": f"{language},{language};q=0.9,en;q=0.7",
        },
    }

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**launch_kwargs)
            ctx = browser.new_context(**context_options)
            try:
                ctx.add_cookies(_CONSENT_COOKIES)
            except Exception:  # noqa: BLE001
                pass
            page = ctx.new_page()
            url = _google_maps_search_url(query, country)
            _emit("Открываю Google Карты…", 0)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except PWTimeout:
                browser.close()
                return []
            page.wait_for_timeout(2_500)
            _accept_consent(page)

            try:
                page.wait_for_selector(_PLACE_LINK, timeout=15_000)
            except PWTimeout:
                log.warning("google_search: лента результатов не появилась (капча?)")
                browser.close()
                return []

            # Скролл ленты до загрузки hard_cap карточек (или пока перестанут расти)
            try:
                page.locator(_PLACE_LINK).first.hover(timeout=3_000)
            except Exception:  # noqa: BLE001
                pass
            prev = 0
            stagnant = 0
            for _ in range(40):
                try:
                    page.mouse.wheel(0, 12_000)
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(2_200)
                n = page.locator(_PLACE_LINK).count()
                _emit(f"Загружено карточек: {n}", len(passed) if filter_fn else len(all_rows))
                if n >= hard_cap:
                    break
                if n == prev:
                    stagnant += 1
                    if stagnant >= 2:
                        break
                else:
                    stagnant = 0
                    prev = n

            hrefs: list[str] = []
            for link in page.locator(_PLACE_LINK).all()[:hard_cap]:
                h = link.get_attribute("href") or ""
                if h:
                    hrefs.append(h)

            # Заходим в каждую карточку и парсим детали
            for href in hrefs:
                if (len(passed) if filter_fn else len(all_rows)) >= target:
                    break
                try:
                    page.goto(href, wait_until="domcontentloaded", timeout=20_000)
                    page.wait_for_timeout(1_800)
                    row = _extract(page, href)
                except Exception as exc:  # noqa: BLE001
                    log.debug("google_search: карточка пропущена: %s", exc)
                    continue
                if not row:
                    continue
                key = (row["name"].lower(), row["address"].lower())
                if key in seen:
                    continue
                seen.add(key)
                all_rows.append(row)
                matched = not filter_fn or filter_fn(row)
                if filter_fn and matched:
                    passed.append(row)
                _emit(
                    f"{'Найдена' if matched else 'Проверяем'}: {row['name'][:40]}",
                    len(passed) if filter_fn else len(all_rows),
                    checked=len(all_rows),
                    organization=row if matched else None,
                )

            browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("google_search.collect failed: %s", exc)

    return passed if filter_fn else all_rows


def collect_company_analysis(source_url: str, options: dict, emit=None) -> dict:
    """Best-effort deep card collector for one public Google Maps place URL."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"company": {}, "module_errors": {"base": "Playwright не установлен"}}

    result = {
        "company": {}, "reviews": [], "photos": [], "services": [], "prices": [],
        "news": [], "module_errors": {},
    }
    launch_kwargs = _launch_kwargs()
    if settings.google_proxy:
        launch_kwargs["proxy"] = {"server": settings.google_proxy}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**launch_kwargs)
            ctx = browser.new_context(**_CONTEXT_OPTS)
            try:
                ctx.add_cookies(_CONSENT_COOKIES)
            except Exception:  # noqa: BLE001
                pass
            page = ctx.new_page()
            if emit:
                emit("progress", module="base", message="Загружаем карточку Google Maps")
            page.goto(source_url, wait_until="domcontentloaded", timeout=35_000)
            page.wait_for_timeout(2_500)
            _accept_consent(page)
            base = _extract(page, source_url)
            result["company"] = base or {}
            if base and base.get("price_range"):
                result["prices"] = [base["price_range"]]

            if options.get("photos"):
                if emit:
                    emit("progress", module="photos", message="Собираем фотографии Google Maps")
                urls = []
                seen = set()
                for image in page.locator('img[src*="googleusercontent"], img[src*="ggpht"]').all()[:80]:
                    src = image.get_attribute("src") or ""
                    if src and src not in seen:
                        seen.add(src)
                        urls.append(src)
                    if len(urls) >= 20:
                        break
                result["photos"] = urls

            if options.get("reviews"):
                if emit:
                    emit("progress", module="reviews", message="Собираем отзывы Google Maps")
                wanted = int(options.get("review_count") or 50)
                try:
                    button = page.query_selector('button[jsaction*="reviewChart"], button[aria-label*="отзыв"], button[aria-label*="review"]')
                    if button:
                        button.click(timeout=5_000)
                        page.wait_for_timeout(2_000)
                    stagnant = 0
                    previous = 0
                    for _ in range(35):
                        cards = page.locator("div.jftiEf")
                        count = cards.count()
                        if count >= wanted:
                            break
                        try:
                            cards.last.scroll_into_view_if_needed(timeout=2_000)
                        except Exception:  # noqa: BLE001
                            page.mouse.wheel(0, 5000)
                        page.wait_for_timeout(1_300)
                        stagnant = stagnant + 1 if count == previous else 0
                        previous = count
                        if stagnant >= 3:
                            break
                    minimum = int(options.get("review_rating_min") or 1)
                    maximum = int(options.get("review_rating_max") or 5)
                    for card in page.locator("div.jftiEf").all():
                        def card_text(selector: str) -> str:
                            try:
                                locator = card.locator(selector).first
                                return (locator.inner_text() or "").strip() if locator.count() else ""
                            except Exception:  # noqa: BLE001
                                return ""

                        def card_attr(selector: str, name: str) -> str:
                            try:
                                locator = card.locator(selector).first
                                return (locator.get_attribute(name) or "").strip() if locator.count() else ""
                            except Exception:  # noqa: BLE001
                                return ""

                        author = card_text(".d4r55")
                        text = card_text(".wiI7pd")
                        date = card_text(".rsqaWe")
                        label = card_attr("span.kvMYJc", "aria-label")
                        match = re.search(r"([1-5])", label)
                        rating = int(match.group(1)) if match else 0
                        if text and minimum <= rating <= maximum:
                            result["reviews"].append({"author": author, "text": text, "date": date, "rating": rating})
                        if len(result["reviews"]) >= wanted:
                            break
                except Exception as exc:  # noqa: BLE001
                    result["module_errors"]["reviews"] = f"Google reviews: {str(exc)[:180]}"
            browser.close()
    except Exception as exc:  # noqa: BLE001
        result["module_errors"]["base"] = f"Google Maps: {type(exc).__name__}: {str(exc)[:200]}"
    return result
