from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Парсер 2ГИС через официальный Catalog API v3 (второй источник живого поиска).

Порт из twogis_parser.py. Адаптация под DataMaps:
  * API-ключ берём из settings.twogis_api_key (с дефолтом, как в оригинале);
  * BrowserSession — из app.services.maps.browser;
  * CLI/export_excel/print_stats/main не переносим — экспорт делает maps/xlsx.py.

Публичные функции: collect(query, max_companies, emit, filter_fn), apply_filters.
"""

import logging
import random
import re
import time
from urllib.parse import quote

import requests

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

# ── API endpoints ─────────────────────────────────────────────────────────
_SEARCH_API = "https://catalog.api.2gis.com/3.0/items"
_BYID_API = "https://catalog.api.2gis.com/3.0/items/byid"
_REVIEWS_API = "https://catalog.api.2gis.com/3.0/items/{id}/reviews"


def _get_api_key() -> str:
    return settings.twogis_api_key or "ruMkgx3275"


# ── HTTP-сессия ────────────────────────────────────────────────────────────
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": _UA,
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": "https://2gis.ru/",
})

# ── Соцсети ────────────────────────────────────────────────────────────────
_SOCIAL_CONTACT_TYPES = frozenset([
    "vkontakte", "instagram", "facebook", "twitter", "odnoklassniki",
    "youtube", "telegram", "tiktok", "ok", "vk", "whatsapp",
])
_SOCIAL_DOMAINS = (
    "vk.com", "vkontakte.ru", "instagram.com", "t.me", "telegram.me",
    "ok.ru", "facebook.com", "fb.com", "youtube.com", "tiktok.com",
    "twitter.com", "x.com", "whatsapp.com",
)


# ── Помощники ──────────────────────────────────────────────────────────────
def _sleep(lo=0.8, hi=2.0):
    time.sleep(random.uniform(lo, hi))


def _fmt_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return raw.strip()


def _clean_site(url: str) -> str:
    url = re.sub(r"^https?://", "", url.strip())
    return url.rstrip("/")


def _is_social_url(url: str) -> bool:
    return any(d in url for d in _SOCIAL_DOMAINS)


def _dedup_key(c: dict) -> tuple:
    return (c.get("name", "").lower(), c.get("address", "").lower())


def _build_map_url(org_id: str, name: str = "", address: str = "") -> str:
    numeric_id = re.sub(r"[^0-9]", "", org_id)
    if numeric_id:
        return f"https://2gis.ru/firm/{numeric_id}"
    return f"https://2gis.ru/search/?q={quote(name + ' ' + address)}"


# ── Контакты из contact_groups ─────────────────────────────────────────────
def _extract_contacts(contact_groups: list) -> tuple[str, str, str]:
    """Вернуть (phones_str, site_str, socials_str) из 2GIS contact_groups."""
    phones: list[str] = []
    site: str = ""
    socials: list[str] = []

    for group in (contact_groups or []):
        for contact in (group.get("contacts") or []):
            ctype = (contact.get("type") or "").lower()
            csubtype = (contact.get("subtype") or "").lower()
            url = contact.get("url") or contact.get("href") or ""
            value = contact.get("value") or contact.get("text") or ""

            if ctype == "phone":
                if value:
                    phones.append(_fmt_phone(value))
                continue

            if (ctype in _SOCIAL_CONTACT_TYPES or
                    csubtype in _SOCIAL_CONTACT_TYPES or
                    (url and _is_social_url(url))):
                if url:
                    c = _clean_site(url)
                    if c not in socials:
                        socials.append(c)
                continue

            if ctype in ("website", "link") and url and not _is_social_url(url):
                if not site:
                    site = _clean_site(url)

    return (
        ", ".join(phones) if phones else "—",
        site if site else "—",
        ", ".join(socials) if socials else "—",
    )


# ── Расписание ─────────────────────────────────────────────────────────────
_DAY_MAP = {
    "Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт",
    "Fri": "Пт", "Sat": "Сб", "Sun": "Вс",
}
_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_schedule(schedule: dict) -> str:
    if not schedule:
        return ""
    if schedule.get("is_24h"):
        return "Круглосуточно"

    items = schedule.get("items")
    if items:
        parts = []
        for item in items:
            days_range = item.get("days") or {}
            d_from = _DAY_MAP.get(days_range.get("from", ""), "")
            d_to = _DAY_MAP.get(days_range.get("to", ""), "")
            wh = item.get("working_hours") or []
            h_str = f"{wh[0]['from']}–{wh[0]['to']}" if wh else ""
            if not h_str:
                continue
            if d_from and d_to and d_from != d_to:
                parts.append(f"{d_from}-{d_to} {h_str}")
            elif d_from:
                parts.append(f"{d_from} {h_str}")
        return ", ".join(parts)

    parts: list[str] = []
    for day_key in _DAY_ORDER:
        day_data = schedule.get(day_key)
        if not isinstance(day_data, dict):
            continue
        wh = day_data.get("working_hours") or []
        h_str = f"{wh[0]['from']}–{wh[0]['to']}" if wh else ""
        if h_str:
            parts.append(f"{_DAY_MAP[day_key]} {h_str}")
    return ", ".join(parts)


# ── Особенности из attribute_groups ────────────────────────────────────────
def _extract_attribute_features(attr_groups: list) -> str:
    feats: list[str] = []
    _negative = {"нет", "no", "false", "0", "недоступно", "unavailable"}
    for group in (attr_groups or []):
        for attr in (group.get("attributes") or []):
            name = (attr.get("name") or "").strip()
            if not name or len(name) > 70:
                continue
            raw_val = attr.get("value")
            if raw_val is not None and str(raw_val).strip().lower() in _negative:
                continue
            feats.append(name)
    return ", ".join(feats[:25])


# ── Search API ─────────────────────────────────────────────────────────────
class _ApiError(Exception):
    def __init__(self, status_code: int, msg: str):
        super().__init__(msg)
        self.status_code = status_code


def _search_page(query: str, page: int) -> list[dict]:
    params = {
        "q": query,
        "page": page,
        "page_size": 10,
        "fields": ("items.point,items.contact_groups,items.rubrics,"
                   "items.reviews,items.photos,items.name_ex"),
        "key": _get_api_key(),
        "locale": "ru_RU",
    }
    try:
        resp = SESSION.get(_SEARCH_API, params=params, timeout=15)
        if resp.status_code in (401, 403):
            raise _ApiError(
                resp.status_code,
                f"2ГИС API вернул {resp.status_code}. "
                f"Проверьте API-ключ (TWOGIS_API_KEY) или зарегистрируйтесь на dev.2gis.ru",
            )
        if not resp.ok:
            log.warning("2GIS search page=%d HTTP %d: %s", page, resp.status_code, resp.text[:300])
            raise _ApiError(resp.status_code, f"2ГИС API вернул {resp.status_code} (страница {page})")
        data = resp.json()
        items = data.get("result", {}).get("items") or data.get("items") or []
        return items
    except _ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("2GIS search page=%d exception: %s", page, exc)
        return []


# ── Парсинг item → company dict ────────────────────────────────────────────
def _parse_item(item: dict) -> dict:
    org_id = str(item.get("id", ""))
    name = (item.get("name") or (item.get("name_ex") or {}).get("primary") or "—")
    address = item.get("address_name") or (item.get("address") or {}).get("name", "—") or "—"

    point = item.get("point") or {}
    lat = round(float(point["lat"]), 6) if point.get("lat") else "—"
    lon = round(float(point["lon"]), 6) if point.get("lon") else "—"

    rubrics = item.get("rubrics") or []
    category = rubrics[0].get("name", "—") if rubrics else "—"

    reviews_obj = item.get("reviews") or {}
    try:
        raw_rating = (reviews_obj.get("general_rating")
                      or reviews_obj.get("org_rating")
                      or reviews_obj.get("rating")
                      or 0)
        rating = round(float(raw_rating), 1)
        rating = rating if rating > 0 else "—"
    except (TypeError, ValueError):
        rating = "—"
    try:
        n_reviews = int(
            reviews_obj.get("general_review_count")
            or reviews_obj.get("org_review_count")
            or reviews_obj.get("count")
            or 0
        )
    except (TypeError, ValueError):
        n_reviews = 0

    phone_str, site, social = _extract_contacts(item.get("contact_groups") or [])

    all_rubrics = item.get("rubric_list") or rubrics
    services = ", ".join(r.get("name", "") for r in all_rubrics if r.get("name"))

    map_url = _build_map_url(org_id, name, address)

    return {
        "id": org_id,
        "oid": org_id,
        "name": name,
        "phone": phone_str,
        "site": site,
        "social": social,
        "address": address,
        "lat": lat,
        "lon": lon,
        "category": category,
        "rating": rating,
        "reviews": n_reviews,
        "hours": "",
        "description": "",
        "services": services,
        "features": "",
        "has_site": "Да" if site != "—" else "Нет",
        "map_url": map_url,
    }


# ── Обогащение через byid ──────────────────────────────────────────────────
def _enrich_byid(company: dict) -> dict:
    org_id = company.get("id", "")
    if not org_id:
        return company
    if company.get("hours") and company.get("description") and company.get("features"):
        return company

    params = {
        "id": org_id,
        "fields": ("items.schedule,items.description_short,"
                   "items.rubric_list,items.attribute_groups,items.photos,"
                   "items.contact_groups"),
        "key": _get_api_key(),
        "locale": "ru_RU",
    }
    try:
        resp = SESSION.get(_BYID_API, params=params, timeout=15)
        if resp.status_code not in (401, 403):
            resp.raise_for_status()
        data = resp.json()
        items = data.get("result", {}).get("items") or data.get("items") or []
        if not items:
            return company
        detail = items[0]
    except Exception as exc:  # noqa: BLE001
        log.debug("byid %s: %s", org_id, exc)
        return company

    if company.get("phone") == "—" or company.get("site") == "—":
        cg = detail.get("contact_groups") or []
        if cg:
            phone_str, site, social = _extract_contacts(cg)
            if company.get("phone") == "—" and phone_str != "—":
                company["phone"] = phone_str
            if company.get("site") == "—" and site != "—":
                company["site"] = site
                company["has_site"] = "Да"
            if company.get("social") == "—" and social != "—":
                company["social"] = social

    if not company.get("hours"):
        schedule = detail.get("schedule")
        if schedule:
            company["hours"] = _parse_schedule(schedule)

    if not company.get("description"):
        desc = detail.get("description_short") or ""
        company["description"] = str(desc)[:200]

    if not company.get("features"):
        company["features"] = _extract_attribute_features(detail.get("attribute_groups") or [])

    rubric_list = detail.get("rubric_list") or []
    if rubric_list:
        company["services"] = ", ".join(r.get("name", "") for r in rubric_list if r.get("name"))

    return company


# ── Контакты с отрендеренной страницы (фолбэк, когда API без contact_groups) ─
_SKIP_DOMAINS = frozenset([
    "2gis.ru", "flamp.ru", "api.", "disk.2gis", "yandex.", "google.",
    "apple.", "schema.org", "w3.org",
])


def _extract_contacts_html(html: str) -> tuple[str, str, str]:
    try:
        from bs4 import BeautifulSoup
        import json as _json
    except ImportError:
        return "—", "—", "—"

    soup = BeautifulSoup(html, "html.parser")
    phones: list[str] = []
    sites: list[str] = []
    socials: list[str] = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = (script.string or "").strip()
            if not raw:
                continue
            data = _json.loads(raw)
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, dict):
                continue

            tel = data.get("telephone") or ""
            if tel:
                fmt = _fmt_phone(str(tel))
                if fmt not in phones:
                    phones.append(fmt)

            for key in ("url", "website"):
                u = data.get(key) or ""
                if u and not any(d in u for d in _SKIP_DOMAINS):
                    if _is_social_url(u):
                        c = _clean_site(u)
                        if c not in socials:
                            socials.append(c)
                    elif not sites:
                        sites.append(_clean_site(u))

            for same in (data.get("sameAs") or []):
                same = str(same)
                if _is_social_url(same):
                    c = _clean_site(same)
                    if c not in socials:
                        socials.append(c)
        except Exception:  # noqa: BLE001
            pass

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if href.startswith("tel:"):
            raw = href[4:].strip()
            if raw:
                fmt = _fmt_phone(raw)
                if fmt not in phones:
                    phones.append(fmt)
        elif href.startswith(("http://", "https://")):
            if any(d in href for d in _SKIP_DOMAINS):
                continue
            if _is_social_url(href):
                c = _clean_site(href)
                if c not in socials:
                    socials.append(c)
            else:
                c = _clean_site(href)
                if c not in sites:
                    sites.append(c)

    return (
        ", ".join(phones[:3]) if phones else "—",
        sites[0] if sites else "—",
        ", ".join(socials[:5]) if socials else "—",
    )


def _enrich_from_jsonld(html: str, company: dict):
    try:
        from bs4 import BeautifulSoup
        import json as _json
    except ImportError:
        return

    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = (script.string or "").strip()
            if not raw:
                continue
            data = _json.loads(raw)
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, dict):
                continue

            if not company.get("description"):
                desc = data.get("description") or ""
                if desc:
                    company["description"] = str(desc)[:200]

            if company.get("rating") == "—":
                agg = data.get("aggregateRating") or {}
                rv = agg.get("ratingValue") or data.get("ratingValue")
                if rv:
                    try:
                        company["rating"] = round(float(str(rv).replace(",", ".")), 1)
                    except (ValueError, TypeError):
                        pass

            if not company.get("reviews") or company["reviews"] == 0:
                agg = data.get("aggregateRating") or {}
                rc = agg.get("reviewCount") or agg.get("ratingCount")
                if rc:
                    try:
                        company["reviews"] = int(rc)
                    except (ValueError, TypeError):
                        pass
        except Exception:  # noqa: BLE001
            pass


# ── Главная collect() ──────────────────────────────────────────────────────
def collect(query: str, max_companies: int = 100, emit=None, filter_fn=None) -> list[dict]:
    def _emit(event, **kw):
        if emit:
            emit(event, **kw)

    try:
        from app.services.maps.browser import BrowserSession
        bs = BrowserSession()
        bs.start()
    except Exception:  # noqa: BLE001
        bs = None

    hard_cap = min(max_companies * 8, 1000) if filter_fn else max_companies
    all_seen: set[tuple] = set()
    all_fetched: list[dict] = []
    passed: list[dict] = []
    streak = 0

    def _target_reached():
        return (len(passed) >= max_companies if filter_fn
                else len(all_fetched) >= max_companies)

    for page in range(1, 150):
        if _target_reached() or len(all_fetched) >= hard_cap:
            break

        _emit("progress",
              found=len(passed) if filter_fn else len(all_fetched),
              checked=len(all_fetched),
              total=max_companies,
              message=f"Загружаем страницу {page}…"
                      + (f" (проверено: {len(all_fetched)})" if filter_fn else ""))

        try:
            items = _search_page(query, page)
        except _ApiError as exc:
            _emit("error", message=str(exc))
            break
        except Exception as exc:  # noqa: BLE001
            _emit("error", message=f"2ГИС: ошибка запроса страницы {page}: {exc}")
            break
        if not items:
            streak += 1
            if streak >= 3:
                break
            _sleep(0.8, 2.0)
            continue
        streak = 0

        for item in items:
            c = _parse_item(item)
            key = _dedup_key(c)
            if key in all_seen:
                continue
            all_seen.add(key)

            _emit("progress",
                  found=len(passed) if filter_fn else len(all_fetched),
                  total=max_companies,
                  message=f"Обогащаем: {c['name'][:40]}…")

            c = _enrich_byid(c)

            needs_contacts = (c.get("phone") == "—" or c.get("site") == "—"
                              or c.get("social") == "—")
            if needs_contacts and bs and bs.available:
                map_url = c.get("map_url", "")
                if map_url:
                    page_html = bs.fetch(map_url, scroll_px=400, wait_ms=3_000, timeout_ms=25_000)
                    if page_html:
                        ph, st, sc = _extract_contacts_html(page_html)
                        if c.get("phone") == "—" and ph != "—":
                            c["phone"] = ph
                        if c.get("site") == "—" and st != "—":
                            c["site"] = st
                            c["has_site"] = "Да"
                        if c.get("social") == "—" and sc != "—":
                            c["social"] = sc
                        _enrich_from_jsonld(page_html, c)

            _sleep(0.4, 1.0)
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

        _sleep(0.6, 1.5)

    if bs:
        bs.stop()

    return passed if filter_fn else all_fetched


# ── Фильтры ────────────────────────────────────────────────────────────────
def apply_filters(companies: list, no_site=False, no_social=False, no_phone=False) -> list:
    if no_site:
        companies = [c for c in companies if c["has_site"] == "Нет"]
    if no_social:
        companies = [c for c in companies if c["social"] == "—"]
    if no_phone:
        companies = [c for c in companies if c["phone"] == "—"]
    return companies
