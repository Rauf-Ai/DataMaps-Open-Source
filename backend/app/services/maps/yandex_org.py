from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Сбор данных по одной организации Яндекс Карт → ZIP-архив для генерации лендинга.

Порт из org_collector.py: догружает отзывы, фото, часы работы, описание,
акции/новости и упаковывает всё в ZIP с готовым промтом для Claude.

Архив содержит:
  info.json      — структурированные данные компании
  reviews.json   — отзывы клиентов
  news.json      — акции / новости
  summary.txt    — читаемая сводка
  PROMPT.md      — готовый промт для генерации лендинга
  README.txt     — инструкция
  photos/        — скачанные фото (если найдены)

Адаптация под DataMaps:
  * вход — org_id (строка), а не company-dict (карточка догружается через
    yandex_search.enrich_one);
  * возвращается абсолютный путь к ZIP (а не относительное имя файла);
  * SESSION/_sleep/_extract_vitrina берём из app.services.maps.yandex_search,
    fetch_rendered — из app.services.maps.browser.
"""

import io
import json
import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from app.services.maps.browser import fetch_rendered
from app.services.maps.yandex_search import SESSION, _extract_vitrina, _sleep, enrich_one

log = logging.getLogger(__name__)

# Yandex avatars CDN: совпадает с URL фото бизнеса/организации.
_PHOTO_RE = re.compile(
    r"https://avatars\.mds\.yandex\.net/get-[a-z][a-z\-]+"
    r"/\d+/[a-zA-Z0-9_\-]+"
    r"(?:/[a-zA-Z0-9_\-\.]+)?",
    re.IGNORECASE,
)


# ── Низкоуровневые помощники ──────────────────────────────────────────────
def _fetch(url: str):
    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code == 200:
            return r
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch %s: %s", url, exc)
    return None


def _parse_jsonld(soup) -> list:
    blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            blocks.append(json.loads(tag.string or ""))
        except Exception:  # noqa: BLE001
            pass
    return blocks


# ── Извлечение данных ─────────────────────────────────────────────────────
def _extract_reviews(soup, jsonld_blocks: list) -> list[dict]:
    reviews: list[dict] = []
    seen: set[str] = set()

    def _add(text, author="", rating="", date=""):
        t = (text or "").strip()
        if t and t not in seen and len(t) >= 15:
            seen.add(t)
            reviews.append({"author": author, "rating": rating, "date": date, "text": t})

    for block in jsonld_blocks:
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                continue
            for entity in ([item] + (item.get("@graph") or [])):
                if not isinstance(entity, dict):
                    continue
                for rev in (entity.get("review") or entity.get("reviews") or []):
                    if not isinstance(rev, dict):
                        continue
                    text = rev.get("reviewBody") or rev.get("description") or ""
                    a = rev.get("author")
                    author = a.get("name", "") if isinstance(a, dict) else str(a or "")
                    ar = rev.get("reviewRating") or {}
                    _add(text, author=author,
                         rating=str(ar.get("ratingValue", "")),
                         date=str(rev.get("datePublished", "")))

    for sel in [
        ".business-review-view__body-text",
        "[class*='review-view__body-text']",
        "[itemprop='reviewBody']",
    ]:
        for el in soup.select(sel):
            _add(el.get_text(strip=True))

    return reviews


def _extract_org_details(soup, jsonld_blocks: list) -> dict:
    info = {"hours": "", "description": ""}

    for block in jsonld_blocks:
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                continue
            for entity in ([item] + (item.get("@graph") or [])):
                if not isinstance(entity, dict):
                    continue
                if not info["description"]:
                    desc = entity.get("description") or ""
                    if isinstance(desc, str) and len(desc) > 10:
                        info["description"] = desc[:2000]
                if not info["hours"]:
                    oh = entity.get("openingHours") or entity.get("openingHoursSpecification")
                    if oh:
                        if isinstance(oh, list):
                            seen_h: list[str] = []
                            for h in oh:
                                s = str(h)
                                if s not in seen_h:
                                    seen_h.append(s)
                            info["hours"] = "; ".join(seen_h)[:400]
                        else:
                            info["hours"] = str(oh)[:400]

    if not info["description"]:
        for sel in [".business-card-description-view__text", "[itemprop='description']"]:
            el = soup.select_one(sel)
            if el:
                info["description"] = el.get_text(strip=True)[:2000]
                break

    if not info["hours"]:
        for sel in ["[class*='business-working-status']", "[itemprop='openingHours']"]:
            els = soup.select(sel)
            if els:
                info["hours"] = "; ".join(e.get_text(strip=True) for e in els)[:400]
                break

    return info


def _extract_from_js_state(html: str) -> dict:
    """Yandex Maps хранит данные организации в script-тегах как JSON (SSR-state).
    Достаём фото, услуги, особенности. Возвращает dict с ключами photos/services/features."""
    result: dict = {"photos": [], "services": [], "features": []}
    seen_photos: set[str] = set()

    photo_re = re.compile(
        r"https://avatars\.mds\.yandex\.net/get-[a-z][a-z\-]+"
        r"/\d+/[a-zA-Z0-9_\-]+"
        r"(?:/[a-zA-Z0-9_\-\.]+)?",
        re.IGNORECASE,
    )

    for sc_match in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
        t = sc_match.group(1)
        if len(t) < 200:
            continue

        for m in photo_re.finditer(t):
            url = m.group(0)
            key = url.rstrip("/").split("/")[-2] if "/" in url else url
            if key not in seen_photos:
                seen_photos.add(key)
                url = re.sub(r"/(?:L|XL|XXL)(/|$)", "/orig\\1", url)
                if not url.endswith("/orig"):
                    url = url.rstrip("/") + "/orig"
                result["photos"].append(url)

        if not result["services"] and (
            "showcase" in t.lower() or "catalog" in t.lower() or "offer" in t.lower()
        ):
            for m in re.finditer(
                r'"(?:title|name)"\s*:\s*"([^"]{3,80})"[^}]{0,200}?"price"\s*:\s*(\d+)',
                t,
            ):
                item_name = m.group(1)
                price = int(m.group(2))
                if price > 0 and item_name and "{" not in item_name:
                    label = f"{item_name} {price:,} ₽".replace(",", " ")
                    if label not in result["services"]:
                        result["services"].append(label)
            result["services"] = result["services"][:25]

        if len(result["photos"]) >= 20:
            break

    return result


def _extract_photo_urls(html: str, max_photos: int = 20) -> list[str]:
    seen_keys: set[str] = set()
    urls: list[str] = []

    def _add(raw: str):
        if len(urls) >= max_photos:
            return
        if "{" in raw or "%" in raw:
            return
        url = re.sub(
            r"/(?:orig|[SMLX]+(?:_height)?|[0-9]+x[0-9]+(?:\.[a-z]+)?)$",
            "",
            raw.rstrip("/"),
        ) + "/XXL_height"
        parts = url.split("/")
        key = parts[5] if len(parts) > 5 else url
        if key not in seen_keys:
            seen_keys.add(key)
            urls.append(url)

    for m in _PHOTO_RE.finditer(html):
        _add(m.group(0))
        if len(urls) >= max_photos:
            break
    return urls


def _extract_photo_urls_from_jsonld(jsonld_blocks: list) -> list[str]:
    """Достаём URL картинок из полей image / photo в JSON-LD."""
    urls: list[str] = []
    seen: set[str] = set()

    def _collect(val):
        if isinstance(val, str) and val.startswith("http") and val not in seen:
            seen.add(val)
            urls.append(val)
        elif isinstance(val, dict):
            _collect(val.get("url") or val.get("contentUrl", ""))
        elif isinstance(val, list):
            for v in val:
                _collect(v)

    for block in jsonld_blocks:
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                continue
            for entity in ([item] + (item.get("@graph") or [])):
                if not isinstance(entity, dict):
                    continue
                _collect(entity.get("image"))
                _collect(entity.get("photo"))
                _collect(entity.get("primaryImageOfPage"))
    return urls


def _unique_image_urls(values: list[str], max_photos: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = str(value or "").strip()
        if not url.startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= max_photos:
            break
    return urls


def extract_gallery_photo_urls(soup, jsonld_blocks: list, max_photos: int = 20) -> list[str]:
    """Return only organization JSON-LD media and explicit gallery images."""
    candidates = _extract_photo_urls_from_jsonld(jsonld_blocks)
    for image in soup.find_all("img", src=True):
        parent = image.parent
        while parent is not None:
            attrs = " ".join(str(value) for value in parent.attrs.values())
            if "gallery" in attrs.casefold():
                candidates.append(image.get("src", ""))
                break
            parent = parent.parent
    return _unique_image_urls(candidates, max_photos)


def _format_offer_price(value: object, currency: object = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    marker = "₽" if str(currency or "").upper() in {"RUB", "RUR", "₽"} else str(currency or "").strip()
    try:
        amount = float(raw.replace(" ", "").replace(",", "."))
    except ValueError:
        return raw
    if amount.is_integer():
        label = f"{int(amount):,}".replace(",", " ")
    else:
        label = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{label} {marker}".strip()


def extract_service_positions(soup, jsonld_blocks: list) -> list[dict]:
    """Read named offers from structured organization data, never page prose."""
    positions: list[dict] = []

    def add_offer(offer: object, inherited_currency: object = "") -> None:
        if not isinstance(offer, dict):
            return
        item = offer.get("itemOffered") if isinstance(offer.get("itemOffered"), dict) else {}
        nested = offer.get("offers") if isinstance(offer.get("offers"), dict) else {}
        name = str(offer.get("name") or item.get("name") or "").strip()
        price = offer.get("price", nested.get("price"))
        currency = offer.get("priceCurrency") or nested.get("priceCurrency") or inherited_currency
        if name:
            position = {"name": name, "price": _format_offer_price(price, currency)}
            if position not in positions:
                positions.append(position)
        for child in offer.get("itemListElement") or []:
            add_offer(child, currency)

    def visit(value: object, inherited_currency: object = "") -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, inherited_currency)
            return
        if not isinstance(value, dict):
            return
        currency = value.get("priceCurrency") or inherited_currency
        for offer in value.get("makesOffer") or []:
            add_offer(offer, currency)
        catalog = value.get("hasOfferCatalog")
        if isinstance(catalog, dict):
            for offer in catalog.get("itemListElement") or []:
                add_offer(offer, currency)
        for item in value.get("@graph") or []:
            visit(item, currency)

    for block in jsonld_blocks:
        visit(block)
    return positions[:25]


def _extract_news(soup, extra_html: str = "") -> list[dict]:
    news: list[dict] = []
    seen: set[str] = set()
    soups = [soup]
    if extra_html:
        soups.append(BeautifulSoup(extra_html, "html.parser"))
    for s in soups:
        for sel in [
            "[class*='story-view__title']",
            "[class*='promotion-view__title']",
            "[class*='action-view__title']",
            "[class*='news-view__title']",
            "[class*='story-snippet__title']",
            "[class*='action-snippet__title']",
            "[class*='promo-item__title']",
            "[class*='promo-card__title']",
        ]:
            for el in s.select(sel):
                text = el.get_text(strip=True)
                if text and 5 < len(text) < 500 and text not in seen:
                    seen.add(text)
                    news.append({"text": text[:500]})

        page_text = s.get_text(strip=True)
        for m in re.finditer(
            r'([А-ЯЁа-яёA-Za-z0-9][^₽\n]{5,200}(?:₽|скидк|акци|вместо|%)[^₽\n]{0,100})',
            page_text,
        ):
            candidate = m.group(1).strip()
            skip_markers = ("Обзор", "ПоискМаршруты", "Товары и услуги", "Витрина",
                            "положительный", "отрицательный", "Рейтинг", "оценок")
            if any(mk in candidate for mk in skip_markers):
                continue
            if re.search(r'[•]\d+%|%положительный|%отрицательный|\d+ отзыв', candidate):
                continue
            promo_words = {"скидк", "акци", "вместо", "бесплатн", "подарок",
                           "all on", "только до", "предложени", "promotio"}
            has_promo = any(w in candidate.lower() for w in promo_words)
            has_rub = "₽" in candidate
            has_pct = "%" in candidate
            if not ((has_rub and has_promo) or (has_pct and has_promo) or
                    (has_rub and len(candidate) < 120)):
                continue
            candidate = re.sub(r'^(Акция|Новость|Скидка)\s*', '', candidate).strip()
            candidate = re.sub(r'\s*Реклама\s*$', '', candidate).strip()
            if len(candidate) > 10 and candidate not in seen:
                seen.add(candidate)
                news.append({"text": candidate[:500]})
            if len(news) >= 20:
                break

    return news[:20]


# ── Главная точка сбора ───────────────────────────────────────────────────
def collect_org_zip(
    oid: str,
    out_dir: Path,
    emit=None,
    max_reviews: int = 100,
    max_photos: int = 20,
    include_reviews: bool = True,
) -> Optional[str]:
    """Собрать все доступные данные по организации и упаковать в ZIP.

    Возвращает абсолютный путь к ZIP-файлу (или None при провале).
    emit — необязательный callable(event, **kwargs) для прогресса.
    """
    def _emit(msg: str):
        if emit:
            emit("progress", message=msg)

    oid = str(oid or "").strip()
    if not oid:
        return None

    # 0 ── Базовая карточка через витрину/JSON-LD ─────────────────────────
    _emit("Загружаем карточку организации…")
    try:
        company = enrich_one(oid)
    except Exception as exc:  # noqa: BLE001
        log.warning("enrich_one(%s) failed: %s", oid, exc)
        company = {}
    name = (company.get("name") or "").strip()
    if not name or name == "—":
        name = "company"

    # 1 ── Главная страница организации ───────────────────────────────────
    _emit(f"Загружаем страницу: {name}…")
    org_url = f"https://yandex.ru/maps/org/{oid}/"
    html = ""
    used_browser = False
    _emit("Открываем браузер для загрузки фото…")
    html = fetch_rendered(
        org_url,
        wait_for_selector="img[src*='avatars.mds.yandex.net']",
        scroll_px=600,
        extra_wait_ms=2_000,
    )
    if html:
        used_browser = True
    if not html:
        if not used_browser:
            _emit("Браузер недоступен, загружаем стандартным способом…")
        resp = _fetch(org_url)
        html = resp.text if resp else ""
    soup = BeautifulSoup(html, "html.parser")
    jsonld = _parse_jsonld(soup)

    # 2 ── Страница отзывов ───────────────────────────────────────────────
    reviews = []
    if include_reviews:
        _sleep(0.5, 1.0)
        _emit("Загружаем страницу отзывов…")
        rev_resp = _fetch(f"https://yandex.ru/maps/org/{oid}/reviews/")
        if rev_resp:
            rsoup = BeautifulSoup(rev_resp.text, "html.parser")
            reviews = _extract_reviews(rsoup, _parse_jsonld(rsoup))
        if not reviews:
            reviews = _extract_reviews(soup, jsonld)
        reviews = reviews[:max_reviews]
        _emit(f"Отзывов: {len(reviews)}")

    # 3 ── Расширенные детали ─────────────────────────────────────────────
    _sleep(0.3, 0.5)
    details = _extract_org_details(soup, jsonld)

    # 4 ── Фото ───────────────────────────────────────────────────────────
    _emit("Ищем фотографии…")
    photo_urls = extract_gallery_photo_urls(soup, jsonld, max_photos)
    _emit(f"Фотографий найдено: {len(photo_urls)}")

    # 5 ── Акции / новости ────────────────────────────────────────────────
    _sleep(0.3, 0.5)
    _emit("Ищем акции…")
    actions_html = ""
    ar = _fetch(f"https://yandex.ru/maps/org/{oid}/actions/")
    if ar:
        actions_html = ar.text
    news = _extract_news(soup, actions_html)
    _emit(f"Акций/новостей: {len(news)}")

    # 6 ── Полный info-dict ───────────────────────────────────────────────
    service_positions = extract_service_positions(soup, jsonld)
    services = company.get("services", "")
    price_range = company.get("price_range", "")
    if not services:
        vitrina_svcs, vitrina_pr = _extract_vitrina(soup)
        if vitrina_svcs:
            services = ", ".join(vitrina_svcs[:25])
        if vitrina_pr and not price_range:
            price_range = vitrina_pr
    if service_positions:
        services = ", ".join(position["name"] for position in service_positions)

    info = {
        "name": name,
        "category": company.get("category", ""),
        "address": company.get("address", ""),
        "phone": company.get("phone", ""),
        "site": company.get("site", ""),
        "social": company.get("social", ""),
        "rating": company.get("rating", ""),
        "reviews_count": company.get("reviews", 0),
        "services": services,
        "service_positions": service_positions,
        "features": company.get("features", ""),
        "price_range": price_range,
        "map_url": company.get("map_url", "") or org_url,
        "lat": company.get("lat", ""),
        "lon": company.get("lon", ""),
        "hours": details.get("hours", ""),
        "description": details.get("description", ""),
    }

    # 7 ── Сборка ZIP (текстовые файлы) ──────────────────────────────────
    _emit("Формируем архив…")
    safe = re.sub(r"[^\w\-а-яА-Я]", "_", name)[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"org_{safe}_{ts}.zip"
    fpath = out_dir / fname

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("info.json", json.dumps(info, ensure_ascii=False, indent=2))
        zf.writestr("reviews.json", json.dumps(reviews, ensure_ascii=False, indent=2))
        zf.writestr("news.json", json.dumps(news, ensure_ascii=False, indent=2))
        zf.writestr("summary.txt", _make_summary(info, reviews, news))
        zf.writestr("PROMPT.md", _make_prompt(info, reviews, news))
        zf.writestr("README.txt", _make_readme())
    fpath.write_bytes(buf.getvalue())

    # 8 ── Скачивание фото в ZIP ──────────────────────────────────────────
    ok = 0
    if photo_urls:
        _emit(f"Скачиваем фото (0/{len(photo_urls)})…")
        buf2 = io.BytesIO(fpath.read_bytes())
        with zipfile.ZipFile(buf2, "a", zipfile.ZIP_STORED) as zf:
            for i, url in enumerate(photo_urls, 1):
                ext = ("webp" if ".webp" in url.lower()
                       else "png" if ".png" in url.lower() else "jpg")
                try:
                    r = SESSION.get(url, timeout=20)
                    ct = r.headers.get("content-type", "")
                    if r.status_code == 200 and "image" in ct:
                        zf.writestr(f"photos/photo_{i:02d}.{ext}", r.content)
                        ok += 1
                        _emit(f"Скачиваем фото ({ok}/{len(photo_urls)})…")
                except Exception as exc:  # noqa: BLE001
                    log.debug("photo %d: %s", i, exc)
                _sleep(0.2, 0.5)
        fpath.write_bytes(buf2.getvalue())
        _emit(f"Фото загружено: {ok}/{len(photo_urls)}")

    return str(fpath)


# ── Генераторы текста ─────────────────────────────────────────────────────
def _make_summary(info: dict, reviews: list, news: list) -> str:
    lines = [
        f"# {info['name']}", "",
        f"Категория:    {info['category']}",
        f"Адрес:        {info['address']}",
        f"Телефон:      {info['phone']}",
        f"Сайт:         {info['site']}",
        f"Соцсети:      {info['social']}",
        f"Рейтинг:      {info['rating']} ({info['reviews_count']} отзывов)",
        f"Часы работы:  {info['hours'] or '—'}",
        f"Цены:         {info['price_range'] or '—'}",
        f"Карты:        {info['map_url']}", "",
        "Описание:",
        info["description"] or "(нет)", "",
        f"Услуги:       {info['services'] or '—'}",
        f"Особенности:  {info['features'] or '—'}",
    ]
    if reviews:
        lines += ["", "─" * 60, f"ОТЗЫВЫ ({len(reviews)} шт.)", "─" * 60]
        for i, r in enumerate(reviews, 1):
            a = r.get("author") or "Аноним"
            rt = r.get("rating") or ""
            d = r.get("date") or ""
            hdr = f"[{i}] {a}" + (f"  ★{rt}" if rt else "") + (f"  ({d})" if d else "")
            lines += ["", hdr, r.get("text", "")]
    if news:
        lines += ["", "─" * 60, f"АКЦИИ И НОВОСТИ ({len(news)} шт.)", "─" * 60]
        for i, n in enumerate(news, 1):
            lines += [f"\n{i}. {n.get('text', '')}"]
    return "\n".join(lines)


def _make_prompt(info: dict, reviews: list, news: list) -> str:
    """Готовый промт для одноразовой генерации лендинга в Claude Code."""
    if reviews:
        rev_parts = []
        for r in reviews[:25]:
            text = (r.get("text") or "").strip()
            if not text:
                continue
            a = r.get("author") or "Аноним"
            rt = r.get("rating") or ""
            d = r.get("date") or ""
            meta = a + (f" ★{rt}" if rt else "") + (f" · {d}" if d else "")
            rev_parts.append(f"> **{meta}**  \n> {text}")
        rev_block = "\n\n".join(rev_parts) if rev_parts else "_Отзывы не найдены._"
    else:
        rev_block = "_Отзывы не найдены._"

    news_block = (
        "\n".join(f"- {n.get('text', '')}" for n in news if n.get("text"))
        or "_Акции не найдены._"
    )

    try:
        stars = "⭐" * round(float(str(info["rating"]).replace(",", ".")))
    except Exception:  # noqa: BLE001
        stars = ""

    first_phone = str(info.get("phone", "")).split(",")[0].strip()
    phone_clean = re.sub(r"[^+\d]", "", first_phone)

    return f"""\
# Создай лендинг для «{info['name']}»

Ты — опытный фронтенд-разработчик и дизайнер. На основе реальных данных ниже создай
профессиональный одностраничный сайт-лендинг.

---

## 📋 Данные компании (источник — Яндекс Карты)

| Поле | Значение |
|---|---|
| **Название** | {info['name']} |
| **Категория** | {info['category']} |
| **Адрес** | {info['address']} |
| **Телефон** | {info['phone']} |
| **Сайт** | {info['site']} |
| **Соцсети** | {info['social']} |
| **Рейтинг** | {info['rating']} {stars} ({info['reviews_count']} отзывов) |
| **Часы работы** | {info['hours'] or '—'} |
| **Диапазон цен** | {info['price_range'] or '—'} |
| **Услуги** | {info['services'] or '—'} |
| **Особенности** | {info['features'] or '—'} |
| **Описание** | {(info['description'] or '—')[:300]} |
| **Яндекс Карты** | {info['map_url']} |

---

## 💬 Реальные отзывы клиентов

{rev_block}

---

## 🎯 Акции и новости

{news_block}

---

## 🛠 Техническое задание

### Технологии
- Один файл **`index.html`** — открывается без сервера через `file://`
- **Tailwind CSS** через CDN (`https://cdn.tailwindcss.com`)
- **Font Awesome 6** через CDN для иконок
- Ванильный JavaScript (без фреймворков и сборщиков)

### Структура страницы (сверху вниз)

**1. Sticky header** — логотип/название, навигация-якоря, кнопка «Позвонить»

**2. Hero** (полный экран):
- Заголовок — название компании
- Слоган — придумай яркий, релевантный тематике «{info['category']}»
- Рейтинг: {info['rating']} {stars} ({info['reviews_count']} отзывов)
- CTA: «📞 Позвонить» → `tel:{phone_clean}` · «📍 На карте» → `{info['map_url']}`
- Фон: `photos/photo_01.jpg` если есть, иначе градиент под тематику

**3. О нас** — описание + 3–4 иконки-преимущества из «Особенностей»:
`{info['features'] or 'Профессионализм, Качество, Опыт'}`

**4. Услуги** — карточки из поля «Услуги»:
`{info['services'] or '(добавь ключевые услуги из категории)'}`
Цены: {info['price_range'] or 'уточняйте'}

**5. Галерея** *(только если папка `photos/` не пустая)*
```html
<img src="photos/photo_01.jpg" alt="Фото">
<img src="photos/photo_02.jpg" alt="Фото">
```
Grid 2–4 колонки, все фото из папки `photos/`.

**6. Отзывы** — карточки с реальными отзывами из раздела выше.
⚠️ Показывай только реальные — не выдумывай.

**7. Акции** *(только если данные есть в разделе «Акции» выше)*
> 💡 Сторис/акции на Яндекс Картах загружаются динамически и не всегда попадают в архив.
> Если пользователь добавил скриншоты сторисов в папку `promo/` — учти их при создании секции акций.
> Иначе используй только данные из `news.json`.

**8. Контакты**
- Адрес: {info['address']}
- Телефон: {info['phone']}
- Соцсети: {info['social']}
- Часы: {info['hours'] or 'уточняйте'}
- Кнопка «Открыть в Яндекс Картах» → `{info['map_url']}`

**9. Footer** — название, год, соцсети-иконки

### Дизайн

- Цветовую палитру подбери под тематику **«{info['category']}»**
  (медицина → синий/белый; красота → пастель; еда → тёплые тона; авто → тёмный/серый)
- Современный вид, не шаблонный
- Mobile-first, полностью адаптивный
- Плавное появление секций при скролле (`IntersectionObserver`)

### Правила контента

| ⛔ Нельзя | ✅ Можно |
|---|---|
| Выдумывать отзывы | Придумывать слоган |
| Добавлять несуществующие услуги | Дописывать текст преимуществ |
| Изменять контакты | Выбирать цветовую схему |
| Придумывать цены | Придумывать заголовки секций |

---

Создай файл `index.html`. Если папка `photos/` не пустая — используй фото.
Если данных (отзывов, акций) нет — скрывай соответствующие секции.
"""


def _make_readme() -> str:
    return """\
Содержимое архива
=================

  info.json     — данные компании (JSON)
  reviews.json  — отзывы клиентов с Яндекс Карт (JSON)
  news.json     — акции и новости (JSON)
  summary.txt   — читаемая сводка всех данных
  PROMPT.md     — готовый промт для Claude Code (создание лендинга)
  README.txt    — этот файл
  photos/       — фотографии компании (если найдены)

Как использовать PROMPT.md
--------------------------

Вариант 1 — Claude Code CLI (рекомендуется):
  1. Распакуй архив: unzip org_*.zip -d landing/
  2. cd landing/
  3. Запусти: claude
  4. Вставь содержимое PROMPT.md в чат Claude Code

Вариант 2 — Через аргумент CLI:
  cd landing/ && claude "$(cat PROMPT.md)"

Вариант 3 — claude.ai/code (веб):
  1. Распакуй архив
  2. Открой claude.ai/code, добавь папку как проект
  3. Вставь PROMPT.md в чат

После генерации index.html:
  - Открой в браузере: open index.html
  - Фото из photos/ подключены автоматически
  - Отредактируй под финальные требования клиента
"""
