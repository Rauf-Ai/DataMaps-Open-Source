from __future__ import annotations
from typing import Optional, Any, Dict, Iterable, List, Literal, Union, Callable

"""Живой парсер Яндекс Карт: поиск организаций через Search-Maps API + обогащение.

Портирован из YandexMapsParser/yandex_parser.py. Отличия от оригинала:
  - API-ключ и URL берутся из app.config.Settings (YANDEX_SEARCH_API_KEY / _URL).
  - BrowserSession импортируется из app.services.maps.browser.
  - CLI/Excel-экспорт вынесены (export → app.services.maps.xlsx).

Все функции синхронные (requests + Playwright sync). На уровне оркестратора
(maps_service) вызовы оборачиваются в asyncio.to_thread.
"""

import json
import logging
import random
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from app.config import get_settings
from app.services.maps.browser import BrowserSession
from app.services.maps.normalization import absolute_external_url, parse_count
from app.services.maps.socials import (
    NETWORK_DOMAINS,
    classify_social_url,
    extract_social_urls,
    normalize_social_url,
)

log = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://yandex.ru/maps/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

SOCIAL_DOMAINS = tuple(domain for domains in NETWORK_DOMAINS.values() for domain in domains)

# JSON-LD @type значения, соответствующие бизнесу
_BUSINESS_TYPES = frozenset([
    "LocalBusiness", "MedicalBusiness", "HealthAndBeautyBusiness",
    "FoodEstablishment", "Restaurant", "CafeOrCoffeeShop", "BarOrPub",
    "Store", "AutoRepair", "Hotel", "Lodging", "BeautySalon",
    "Dentist", "MedicalOrganization", "HealthClub", "SportsActivityLocation",
    "TouristAttraction", "Organization",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sleep(lo=0.8, hi=2.0):
    time.sleep(random.uniform(lo, hi))


def _fmt_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return raw.strip()


def _review_count_from_html(html: str) -> int:
    """Read a localized review count from static or rendered Yandex markup."""
    return parse_count(html)


def _is_social(url: str) -> bool:
    return classify_social_url(url) is not None


def _is_valid_social(href: str) -> bool:
    """True only for concrete, public business social profiles."""
    return normalize_social_url(href) is not None


def _merge_social_urls(company: dict, candidates: Iterable[str]) -> None:
    """Append valid social profiles to a card, retaining its current order."""
    urls = extract_social_urls(company.get("social", ""))
    for candidate in candidates:
        for url in extract_social_urls(candidate):
            if url not in urls:
                urls.append(url)
    company["social"] = ", ".join(urls) if urls else "—"


def _dedup_key(c: dict) -> tuple:
    return (c.get("name", "").lower(), c.get("address", "").lower())


def _extract_oid(uri: str) -> str:
    m = re.search(r"oid=(\d+)", uri or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Step 1 — Search API
# ---------------------------------------------------------------------------
def _search_page(query: str, skip: int) -> list[dict]:
    api_key = settings.yandex_search_api_key
    if not api_key:
        log.warning("YANDEX_SEARCH_API_KEY не задан — живой поиск недоступен")
        return []
    params = {
        "apikey": api_key,
        "text": query,
        "lang": "ru_RU",
        "type": "biz",
        "results": 10,
        "skip": skip,
    }
    try:
        resp = SESSION.get(settings.yandex_search_api_url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("features", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("Search API skip=%d: %s", skip, exc)
        return []


def _parse_feature(feat: dict) -> dict:
    props = feat.get("properties", {})
    geo = feat.get("geometry", {})

    name = props.get("name", "—")
    name = re.sub(r'^["\'«»]+|["\'»«]+$', "", name).strip() or "—"

    coords = geo.get("coordinates", [])
    lon = round(coords[0], 6) if len(coords) > 0 and coords[0] is not None else "—"
    lat = round(coords[1], 6) if len(coords) > 1 and coords[1] is not None else "—"

    meta = props.get("CompanyMetaData", {})
    address = meta.get("address", "—")
    address_meta = meta.get("Address") or {}
    components = address_meta.get("Components") or [] if isinstance(address_meta, dict) else []
    localities = [
        str(component.get("name") or "").strip()
        for component in components
        if isinstance(component, dict) and component.get("kind") == "locality"
    ]
    provinces = [
        str(component.get("name") or "").strip()
        for component in components
        if isinstance(component, dict)
        and component.get("kind") == "province"
        and "федеральный округ" not in str(component.get("name") or "").casefold()
    ]
    city = next((value for value in reversed(localities) if value), "")
    region = next((value for value in reversed(provinces) if value), "")
    countries = [
        str(component.get("name") or "").strip()
        for component in components
        if isinstance(component, dict) and component.get("kind") == "country"
    ]
    country = next((value for value in reversed(countries) if value), "")
    cats = meta.get("Categories", [])
    category = cats[0].get("name", "—") if cats else "—"
    oid = _extract_oid(props.get("uri", "")) or str(meta.get("id", ""))

    chain = meta.get("Chain") or meta.get("chain") or meta.get("Network") or meta.get("network") or {}
    if not chain and isinstance(meta.get("Chains"), list) and meta["Chains"]:
        chain = meta["Chains"][0]
    if isinstance(chain, dict):
        chain_id = str(chain.get("id") or chain.get("chainId") or "")
        chain_name = str(chain.get("name") or chain.get("title") or "")
    else:
        chain_id = ""
        chain_name = str(chain or "")

    phones = [_fmt_phone(p["formatted"]) for p in meta.get("Phones", [])
              if p.get("formatted")]
    phone_str = ", ".join(phones) if phones else "—"

    rating, reviews = "—", 0
    for path in [
        lambda m: m.get("rating") or {},
        lambda m: m.get("Siren", {}).get("Reviews", {}),
    ]:
        obj = path(meta)
        r_val = (obj.get("ratings") or obj.get("score")
                 or obj.get("value") or obj.get("rating"))
        rv_val = obj.get("reviews") or obj.get("count") or 0
        if r_val:
            try:
                rating = round(float(r_val), 1)
                reviews = int(rv_val) if rv_val else 0
                break
            except (TypeError, ValueError):
                pass

    if rating == "—":
        top_r = props.get("rating") or {}
        r_val = top_r.get("ratings") or top_r.get("score")
        if r_val:
            try:
                rating = round(float(r_val), 1)
                reviews = int(top_r.get("reviews") or top_r.get("count") or 0)
            except (TypeError, ValueError):
                pass

    site = meta.get("url", "") or ""
    social_candidates: list[str] = []
    for link in meta.get("Links", []):
        href = (link.get("href") or link.get("url") or "").strip()
        if not href:
            continue
        social_candidates.append(href)

    site = absolute_external_url(site) or "—"

    map_url = (f"https://yandex.ru/maps/org/{oid}/" if oid
               else f"https://yandex.ru/maps/?text={quote(name + ' ' + address)}")

    company = {
        "oid": oid,
        "name": name,
        "phone": phone_str,
        "site": site,
        "social": "—",
        "address": address,
        "country": country,
        "region": region,
        "city": city,
        "lat": lat,
        "lon": lon,
        "category": category,
        "rating": rating,
        "reviews": reviews,
        "has_site": "Да" if site != "—" else "Нет",
        "map_url": map_url,
        "services": "",
        "features": "",
        "price_range": "",
        "chain_id": chain_id,
        "chain_name": chain_name,
    }
    _merge_social_urls(company, social_candidates)
    return company


# ---------------------------------------------------------------------------
# Витрина (showcase) text parser
# ---------------------------------------------------------------------------
def _extract_vitrina(soup) -> tuple:
    """Разбор названий услуг и цен из секции «Витрина» страницы Яндекс Карт.

    Возвращает (services_list, price_range_str). Витрина рендерится React-ом без
    стабильных CSS-классов; данные присутствуют в SSR-тексте в виде
    "Витрина<name1><price1>₽<name2><price2>₽...".
    """
    text = soup.get_text(strip=True)

    _SECTION_RE = re.compile(
        r'(?:Витрин[аы]|Товары\s+и\s+услуги|Прайс(?:-лист)?|'
        r'Цены|Услуги|Прейскурант|Стоимость\s+услуг)'
        r'(.{10,3000})',
        re.IGNORECASE,
    )
    m = _SECTION_RE.search(text)
    if m:
        section = m.group(1)
    else:
        _PRICE_PAIR_RE = re.compile(
            r'[А-ЯЁа-яё][^\d₽]{2,80}?\d[\d\s\xa0]{0,15}₽'
        )
        pos = 0
        section = ""
        while pos < len(text) - 100:
            window = text[pos:pos + 800]
            matches = list(_PRICE_PAIR_RE.finditer(window))
            if len(matches) >= 3:
                section = window[matches[0].start():]
                break
            pos += 200
        if not section:
            return [], ""

    pairs = re.findall(
        r'([А-ЯЁа-яё][^\d₽]{2,100}?)(\d[\d\s\xa0]{0,15}₽)',
        section,
    )

    services: list[str] = []
    prices: list[int] = []
    seen: set[str] = set()
    _skip = {
        "обзор", "фото", "отзывы", "контакты", "маршрут",
        "позвонить", "записаться", "все товары и услуги",
        "встречайте ленту", "toll road", "платная дорога",
        "посмотреть все", "показать все",
    }

    for raw_name, raw_price in pairs[:25]:
        name = raw_name.strip().rstrip(" \t\n\r")
        if not name or name.lower() in _skip or len(name) < 3:
            continue
        if "{" in name or ":" in name:
            continue
        price_str = raw_price.replace("\xa0", " ").strip()
        label = f"{name} — {price_str}"
        if label in seen:
            continue
        seen.add(label)
        services.append(label)
        try:
            prices.append(int(re.sub(r"[^\d]", "", price_str)))
        except ValueError:
            pass

    price_range = ""
    if prices:
        mn, mx = min(prices), max(prices)
        if mn == mx:
            price_range = f"{mn:,} ₽".replace(",", " ")
        else:
            price_range = f"от {mn:,} ₽".replace(",", " ")

    return services, price_range


# ---------------------------------------------------------------------------
# Step 2 — Per-org enrichment (JSON-LD + itemprop + HTML fallback)
# ---------------------------------------------------------------------------
def _enrich(company: dict, browser_session=None) -> dict:
    oid = company.get("oid", "")
    if not oid:
        return company

    ns = company["site"] == "—"
    nso = company["social"] == "—"
    nr = company["rating"] == "—"
    nrv = company["reviews"] == 0
    nsv = not company.get("services")
    nft = not company.get("features")
    no_pr = not company.get("price_range")

    if not any([ns, nso, nr, nrv, nsv, nft, no_pr]):
        return company

    try:
        resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/", timeout=15)
        if resp.status_code != 200:
            return company
        html = resp.text
    except Exception as exc:  # noqa: BLE001
        log.debug("Enrich %s: %s", oid, exc)
        return company

    soup = BeautifulSoup(html, "html.parser")

    # ── 1. JSON-LD ────────────────────────────────────────────────────────
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, AttributeError):
            continue
        _apply_jsonld(company, data)
        ns = company["site"] == "—"
        nr = company["rating"] == "—"
        nrv = company["reviews"] == 0

    # ── 2. itemprop / schema.org attributes ───────────────────────────────
    if nr:
        el = soup.find(itemprop="ratingValue")
        if el:
            try:
                company["rating"] = round(float(
                    (el.get("content") or el.get_text()).replace(",", ".")), 1)
                nr = False
            except ValueError:
                pass

    if nrv:
        el = (soup.find(itemprop="reviewCount") or
              soup.find(itemprop="ratingCount"))
        if el:
            try:
                v = int(re.sub(r"\D", "", el.get("content") or el.get_text()))
                if v > 0:
                    company["reviews"] = v
                    nrv = False
            except ValueError:
                pass

    if nrv:
        count = _review_count_from_html(html)
        if count:
            company["reviews"] = count
            nrv = False

    if ns:
        el = soup.find(itemprop="url")
        if el:
            href = el.get("href", "") or el.get("content", "")
            if href and not _is_social(href) and "yandex" not in href:
                site = absolute_external_url(href)
                if site:
                    company["site"] = site
                    company["has_site"] = "Да"
                    ns = False

    # ── 3. Соцсети ────────────────────────────────────────────────────────
    contact_sections = (
        soup.select(".business-contacts__social a") or
        soup.select(".business-contacts a") or
        soup.select("[class*='contacts'] a") or
        soup.select("[class*='link-source'] a") or
        []
    )
    _merge_social_urls(
        company,
        [a.get("href", "").strip() for a in contact_sections],
    )
    _merge_social_urls(
        company,
        [a["href"].strip() for a in soup.find_all("a", href=True)],
    )
    # React hydration data holds contacts which are not represented as anchors.
    _merge_social_urls(company, [html])

    # ── 4. CSS class selectors as last resort ─────────────────────────────
    if nr:
        for sel in [".business-rating-badge-view__rating",
                    "[class*='rating__value']", ".business-rating__value"]:
            el = soup.select_one(sel)
            if el:
                try:
                    company["rating"] = round(float(
                        el.get_text(strip=True).replace(",", ".")), 1)
                    break
                except ValueError:
                    pass

    if nrv:
        for sel in [".business-rating-badge-view__count",
                    "[class*='rating__count']"]:
            el = soup.select_one(sel)
            if el:
                try:
                    v = int(re.sub(r"\D", "", el.get_text()))
                    if v > 0:
                        company["reviews"] = v
                        break
                except ValueError:
                    pass

    # ── 5. Товары и услуги (витрина из статичного HTML) ───────────────────
    if not company.get("services"):
        svcs, pr = _extract_vitrina(soup)
        if svcs:
            company["services"] = ", ".join(svcs[:25])
        if pr and not company.get("price_range"):
            company["price_range"] = pr

    # ── 6. Особенности ────────────────────────────────────────────────────
    _FTR_VALUE_WORDS = {"доступно", "недоступно", "да", "нет", "yes", "no", "true", "false"}
    if not company.get("features"):
        for sel in [
            ".business-features-view__feature",
            "[class*='features-view__feature']",
        ]:
            items = []
            for el in soup.select(sel):
                t = el.get_text(strip=True)
                if not t:
                    continue
                if "₽" in t or "руб" in t.lower():
                    continue
                if t.lower() in _FTR_VALUE_WORDS:
                    continue
                if len(t) > 70:
                    continue
                items.append(t)
            if items:
                company["features"] = ", ".join(items[:25])
                break

    # ── 7. Цены (CSS fallback) ────────────────────────────────────────────
    if not company.get("price_range"):
        for sel in [
            ".business-prices-view__text",
            "[class*='price-view__text']",
            "[itemprop='priceRange']",
        ]:
            el = soup.select_one(sel)
            if el:
                txt = el.get_text(strip=True)
                if txt and len(txt) < 60:
                    company["price_range"] = txt
                    break

    # ── 8. Browser fallback — витрина JS-rendered для большинства орг. ─────
    if (nrv or not company.get("services") or company["social"] == "—") and browser_session and browser_session.available:
        rendered = browser_session.fetch(
            f"https://yandex.ru/maps/org/{oid}/",
            scroll_px=400,
            wait_ms=1_500,
        )
        if rendered:
            rsoup = BeautifulSoup(rendered, "html.parser")
            _merge_social_urls(company, [rendered])
            if nrv:
                count = _review_count_from_html(rendered)
                if count:
                    company["reviews"] = count
                    nrv = False
            svcs, pr = _extract_vitrina(rsoup)
            if svcs:
                company["services"] = ", ".join(svcs[:25])
            if pr and not company.get("price_range"):
                company["price_range"] = pr

    return company


def _apply_jsonld(company: dict, data):
    """Рекурсивно применить JSON-LD к company dict."""
    if isinstance(data, list):
        for item in data:
            _apply_jsonld(company, item)
        return
    if not isinstance(data, dict):
        return

    ar = data.get("aggregateRating") or {}
    if isinstance(ar, dict):
        rv = ar.get("ratingValue")
        rc = ar.get("reviewCount") or ar.get("ratingCount")
        if rv and company["rating"] == "—":
            try:
                company["rating"] = round(float(str(rv).replace(",", ".")), 1)
            except ValueError:
                pass
        if rc and company["reviews"] == 0:
            try:
                v = int(re.sub(r"\D", "", str(rc)))
                if v > 0:
                    company["reviews"] = v
            except ValueError:
                pass

    entity_type = data.get("@type", "")
    if isinstance(entity_type, list):
        entity_type = " ".join(entity_type)
    is_business = any(t in entity_type for t in _BUSINESS_TYPES)
    is_website = entity_type in ("WebSite", "WebPage")

    if not is_website and company["site"] == "—":
        url = data.get("url") or data.get("website") or ""
        if (isinstance(url, str) and url.startswith("http")
                and not _is_valid_social(url) and "yandex" not in url):
            site = absolute_external_url(url)
            if site:
                company["site"] = site
                company["has_site"] = "Да"

    if is_business or (not is_website and not entity_type):
        same = data.get("sameAs") or []
        if isinstance(same, str):
            same = [same]
        _merge_social_urls(
            company,
            [link for link in same if isinstance(link, str)],
        )

    if not company.get("services"):
        catalog = data.get("hasOfferCatalog") or {}
        if isinstance(catalog, dict):
            items = catalog.get("itemListElement") or []
            if isinstance(items, list):
                names = [i.get("name", "") for i in items if isinstance(i, dict) and i.get("name")]
                if names:
                    company["services"] = ", ".join(names[:25])
        if not company.get("services"):
            offers = data.get("makesOffer") or []
            if isinstance(offers, list):
                names = []
                for o in offers:
                    if not isinstance(o, dict):
                        continue
                    n = o.get("name") or (o.get("itemOffered") or {}).get("name", "")
                    if n:
                        names.append(n)
                if names:
                    company["services"] = ", ".join(names[:25])

    if not company.get("price_range"):
        pr = data.get("priceRange")
        if isinstance(pr, str) and pr.strip():
            company["price_range"] = pr.strip()[:50]
        else:
            offers_list = data.get("makesOffer") or []
            if not offers_list:
                cat = data.get("hasOfferCatalog") or {}
                if isinstance(cat, dict):
                    offers_list = cat.get("itemListElement") or []
            if isinstance(offers_list, list) and offers_list:
                prices = []
                for o in offers_list:
                    if not isinstance(o, dict):
                        continue
                    p = o.get("price") or (o.get("offers") or {}).get("price")
                    if p is not None:
                        try:
                            prices.append(float(str(p).replace(",", ".")))
                        except (ValueError, TypeError):
                            pass
                if prices:
                    mn, mx = int(min(prices)), int(max(prices))
                    cur = data.get("priceCurrency") or "₽"
                    if cur == "RUB":
                        cur = "₽"
                    company["price_range"] = (
                        f"{mn} {cur}" if mn == mx else f"{mn}–{mx} {cur}"
                    )

    if not company.get("features"):
        amenity = data.get("amenityFeature") or []
        if isinstance(amenity, list):
            _ftr_skip = {"false", "0", "no", "none", "", "недоступно"}
            values = []
            for a in amenity:
                if not isinstance(a, dict):
                    continue
                val = a.get("value")
                name = a.get("name", "").strip()
                if str(val).lower() in _ftr_skip:
                    continue
                if not name or "₽" in name or "руб" in name.lower():
                    continue
                if len(name) > 70:
                    continue
                values.append(name)
            if values:
                company["features"] = ", ".join(values[:25])

    graph = data.get("@graph")
    if graph:
        _apply_jsonld(company, graph)


# ---------------------------------------------------------------------------
# HTML fallback scraper
# ---------------------------------------------------------------------------
def _scrape_page(query: str, page: int) -> list[dict]:
    url = f"https://yandex.ru/maps/?text={quote(query)}&page={page}"
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("HTML page %d: %s", page, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    companies = []
    for item in soup.select("li.search-list-item"):
        def txt(sel):
            el = item.select_one(sel)
            return el.get_text(strip=True) if el else "—"

        name = txt(".search-business-snippet-view__title")
        address = txt(".search-business-snippet-view__address")
        category = txt(".search-business-snippet-view__category")

        try:
            rating = round(float(
                txt(".business-rating-badge-view__rating").replace(",", ".")), 1)
        except ValueError:
            rating = "—"
        try:
            reviews = int(re.sub(r"\D", "",
                                 txt(".business-rating-badge-view__count")))
        except ValueError:
            reviews = 0

        oid = ""
        for attr in ("data-permalink", "data-uri"):
            m = re.search(r"(\d{10,})", item.get(attr, ""))
            if m:
                oid = m.group(1)
                break

        map_url = (f"https://yandex.ru/maps/org/{oid}/" if oid
                   else f"https://yandex.ru/maps/?text={quote(name + ' ' + address)}")

        companies.append({
            "oid": oid, "name": name, "phone": "—",
            "site": "—", "social": "—", "address": address,
            "lat": "—", "lon": "—", "category": category,
            "rating": rating, "reviews": reviews,
            "has_site": "Нет", "map_url": map_url,
            "services": "", "features": "", "price_range": "",
        })
    return companies


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------
def collect(query: str, max_companies: int = 100,
            emit=None, filter_fn=None) -> list[dict]:
    """Собрать организации по запросу.

    filter_fn — опциональный callable(company) → bool. Если задан, поиск идёт пока
    max_companies не пройдут фильтр или источник не исчерпает выдачу. Защитный
    предел достаточно велик, чтобы малый target не обрывал поиск после нескольких
    десятков неподходящих карточек. Без filter_fn возвращает все собранные.
    """
    def _emit(event, **kw):
        if emit:
            emit(event, **kw)

    try:
        _bs = BrowserSession()
        if _bs.start():
            _emit("progress", found=0, total=max_companies,
                  message="Браузер запущен для парсинга услуг…")
        else:
            _bs = None
    except Exception:  # noqa: BLE001
        _bs = None

    hard_cap = min(max(max_companies * 25, 200), 1000) if filter_fn else max_companies

    all_seen: set[tuple] = set()
    all_fetched: list[dict] = []
    passed: list[dict] = []
    skip, page, streak = 0, 1, 0

    def _target_reached():
        if filter_fn:
            return len(passed) >= max_companies
        return len(all_fetched) >= max_companies

    while not _target_reached():
        if len(all_fetched) >= hard_cap:
            _emit("progress",
                  found=len(passed) if filter_fn else len(all_fetched),
                  total=max_companies,
                  message="Достигнут лимит проверенных компаний.")
            break

        _emit("progress",
              found=len(passed) if filter_fn else len(all_fetched),
              checked=len(all_fetched),
              total=max_companies,
              message=f"Поиск результатов {skip + 1}–{skip + 10}…"
                      + (f" (проверено: {len(all_fetched)})" if filter_fn else ""))

        feats = _search_page(query, skip)
        batch = [_parse_feature(f) for f in feats]

        if not batch:
            _emit("progress",
                  found=len(passed) if filter_fn else len(all_fetched),
                  checked=len(all_fetched),
                  total=max_companies,
                  message=f"API пустой, HTML fallback (стр. {page})…")
            batch = _scrape_page(query, page)

        if not batch:
            streak += 1
            if streak >= 3:
                _emit("progress",
                      found=len(passed) if filter_fn else len(all_fetched),
                      total=max_companies,
                      message="Яндекс больше не отдаёт результаты.")
                break
        else:
            streak = 0

        for c in batch:
            key = _dedup_key(c)
            if key in all_seen:
                continue
            all_seen.add(key)

            _emit("progress",
                  found=len(passed) if filter_fn else len(all_fetched),
                  total=max_companies,
                  message=f"Обогащаем: {c['name'][:40]}…")
            c = _enrich(c, browser_session=_bs)
            _sleep(0.4, 1.2)
            all_fetched.append(c)

            matched = not filter_fn or filter_fn(c)
            if filter_fn and matched:
                passed.append(c)
            if matched:
                _emit(
                    "match",
                    found=len(passed) if filter_fn else len(all_fetched),
                    checked=len(all_fetched),
                    organization=c,
                    message=f"Найдена: {c['name'][:50]}",
                )
            if _target_reached() or len(all_fetched) >= hard_cap:
                break

        skip += 10
        page += 1
        _sleep(0.8, 2.0)

    if _bs:
        _bs.stop()

    return passed if filter_fn else all_fetched


def enrich_one(oid: str) -> dict:
    """Обогатить одну организацию по её Yandex org_id. Возвращает company dict.

    Используется эндпоинтом /api/parser/enrich. Создаёт собственную BrowserSession.
    """
    company = {
        "oid": str(oid),
        "name": "—", "phone": "—", "site": "—", "social": "—",
        "address": "—", "lat": "—", "lon": "—", "category": "—",
        "rating": "—", "reviews": 0,
        "has_site": "Нет", "map_url": f"https://yandex.ru/maps/org/{oid}/",
        "services": "", "features": "", "price_range": "",
    }
    # Сначала пробуем взять имя/адрес/категорию из Search API по самому org_id —
    # часто org-страница в JSON-LD содержит name, но не всегда. Обогащаем напрямую.
    _bs = None
    try:
        _bs = BrowserSession()
        if not _bs.start():
            _bs = None
    except Exception:  # noqa: BLE001
        _bs = None
    try:
        # Берём имя/адрес/рейтинг из JSON-LD страницы org.
        try:
            resp = SESSION.get(f"https://yandex.ru/maps/org/{oid}/", timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup.find_all("script", type="application/ld+json"):
                    try:
                        data = json.loads(tag.string or "")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    _fill_basic_from_jsonld(company, data)
        except Exception as exc:  # noqa: BLE001
            log.debug("enrich_one basic %s: %s", oid, exc)
        company = _enrich(company, browser_session=_bs)
    finally:
        if _bs:
            _bs.stop()
    return company


def _fill_basic_from_jsonld(company: dict, data) -> None:
    """Заполнить name/address/category/phone из JSON-LD (для enrich_one)."""
    if isinstance(data, list):
        for item in data:
            _fill_basic_from_jsonld(company, item)
        return
    if not isinstance(data, dict):
        return
    if company["name"] == "—" and data.get("name"):
        company["name"] = str(data["name"]).strip()
    addr = data.get("address")
    if company["address"] == "—" and addr:
        if isinstance(addr, dict):
            parts = [addr.get("streetAddress"), addr.get("addressLocality")]
            company["address"] = ", ".join(p for p in parts if p) or "—"
        elif isinstance(addr, str):
            company["address"] = addr.strip()
    if company["phone"] == "—" and data.get("telephone"):
        company["phone"] = _fmt_phone(str(data["telephone"]))
    graph = data.get("@graph")
    if graph:
        _fill_basic_from_jsonld(company, graph)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def apply_filters(companies: list[dict],
                  no_site=False, no_social=False, no_phone=False) -> list[dict]:
    result = companies
    if no_site:
        result = [c for c in result if c["has_site"] == "Нет"]
    if no_social:
        result = [c for c in result if c["social"] == "—"]
    if no_phone:
        result = [c for c in result if c["phone"] == "—"]
    return result
