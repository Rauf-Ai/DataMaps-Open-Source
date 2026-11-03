from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Парсер отзывов с Yandex Карт через обычные HTTP-запросы.

Подход взят из рабочего yandex_parser/org_collector — Яндекс пре-рендерит отзывы
для SEO в HTML страницы /reviews/ как JSON-LD блоки и в селекторы вида
`.business-review-view__body-text`. Никакого Selenium/Chromium — обычная сессия
requests с правильными заголовками.
"""

import json
import logging
import random
import re
import time

import requests
from bs4 import BeautifulSoup


log = logging.getLogger(__name__)


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://yandex.ru/maps/",
}


def extract_org_id(yandex_card_url: Optional[str]) -> Optional[str]:
    """Из URL `https://yandex.ru/maps/org/<slug>/<id>/` извлекает числовой id."""
    if not yandex_card_url:
        return None
    m = re.search(r"/maps/org/(?:[^/]+/)?(\d{6,})", yandex_card_url)
    if m:
        return m.group(1)
    m = re.search(r"(\d{8,})", yandex_card_url)
    return m.group(1) if m else None


def _parse_jsonld(soup: BeautifulSoup) -> list:
    blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            blocks.append(json.loads(tag.string or ""))
        except (json.JSONDecodeError, ValueError):
            pass
    return blocks


def _walk_entities(data):
    """Итерируется по всем словарным сущностям в JSON-LD блоке (включая @graph)."""
    if isinstance(data, list):
        for x in data:
            yield from _walk_entities(x)
    elif isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if graph:
            yield from _walk_entities(graph)


def _extract_reviews_jsonld(jsonld_blocks: list) -> list[dict]:
    """Достаёт отзывы из JSON-LD (Schema.org Review). Самый стабильный путь."""
    out: list[dict] = []
    seen_texts: set[str] = set()
    for block in jsonld_blocks:
        for entity in _walk_entities(block):
            for rev in (entity.get("review") or entity.get("reviews") or []):
                if not isinstance(rev, dict):
                    continue
                text = (rev.get("reviewBody") or rev.get("description") or "").strip()
                if not text or text in seen_texts:
                    continue
                seen_texts.add(text)
                a = rev.get("author")
                author = a.get("name", "") if isinstance(a, dict) else str(a or "")
                rating_obj = rev.get("reviewRating") or {}
                try:
                    stars = float(str(rating_obj.get("ratingValue") or 0))
                except (ValueError, TypeError):
                    stars = 0
                out.append({
                    "name": author,
                    "stars": stars,
                    "date": str(rev.get("datePublished") or ""),
                    "text": text,
                    "answer": None,
                })
    return out


def _extract_reviews_html(soup: BeautifulSoup) -> list[dict]:
    """HTML-fallback: парсит блоки `.business-review-view` если они есть в SSR."""
    out: list[dict] = []
    seen_texts: set[str] = set()

    blocks = (
        soup.select(".business-review-view")
        or soup.select("[class*='review-view']")
    )

    for el in blocks:
        text_el = (
            el.select_one(".business-review-view__body-text")
            or el.select_one("[class*='review-view__body-text']")
            or el.select_one("[itemprop='reviewBody']")
        )
        text = text_el.get_text(strip=True) if text_el else ""
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)

        name_el = el.select_one("[itemprop='name']") or el.select_one("[class*='author-name']")
        author = name_el.get_text(strip=True) if name_el else ""

        date_el = el.select_one("meta[itemprop='datePublished']") or el.select_one("[class*='date']")
        date = (date_el.get("content") if date_el and date_el.has_attr("content")
                else (date_el.get_text(strip=True) if date_el else ""))

        stars = 0.0
        rating_el = el.select_one("meta[itemprop='ratingValue']")
        if rating_el and rating_el.has_attr("content"):
            try:
                stars = float(rating_el["content"])
            except (ValueError, TypeError):
                pass
        if not stars:
            star_spans = el.select(".business-rating-badge-view__stars span._full, "
                                   ".business-rating-badge-view__star._full")
            stars = float(len(star_spans)) if star_spans else 0.0

        answer_el = el.select_one(".business-review-comment-content__bubble")
        answer = answer_el.get_text(strip=True) if answer_el else None

        out.append({
            "name": author,
            "stars": stars,
            "date": date,
            "text": text,
            "answer": answer,
        })
    return out


def _extract_company_info(jsonld_blocks: list, soup: BeautifulSoup) -> dict:
    info = {"name": "", "rating": 0.0, "count_rating": 0}
    for block in jsonld_blocks:
        for entity in _walk_entities(block):
            if not info["name"]:
                n = entity.get("name")
                if isinstance(n, str) and n:
                    info["name"] = n
            ar = entity.get("aggregateRating") or {}
            if isinstance(ar, dict):
                if not info["rating"]:
                    try:
                        info["rating"] = float(str(ar.get("ratingValue") or 0).replace(",", "."))
                    except (ValueError, TypeError):
                        pass
                if not info["count_rating"]:
                    rc = ar.get("reviewCount") or ar.get("ratingCount") or 0
                    try:
                        info["count_rating"] = int(re.sub(r"\D", "", str(rc)) or 0)
                    except ValueError:
                        pass
    if not info["name"]:
        el = soup.select_one("h1.orgpage-header-view__header") or soup.select_one("h1")
        if el:
            info["name"] = el.get_text(strip=True)
    return info


def is_captcha_html(html: str) -> bool:
    low = html.lower()
    return "smartcaptcha" in low or "showcaptcha" in low or "captcha-page" in low


def parse_dom(html: str, *, stars_min: int = 1, stars_max: int = 5) -> dict:
    """Общий извлекатель: HTML → {company_info, company_reviews}.

    Используется как HTTP-парсером, так и Playwright-парсером (deep-режим).
    """
    soup = BeautifulSoup(html, "html.parser")
    jsonld = _parse_jsonld(soup)
    reviews = _extract_reviews_jsonld(jsonld)
    if not reviews:
        reviews = _extract_reviews_html(soup)
    if stars_min > 1 or stars_max < 5:
        reviews = [r for r in reviews if stars_min <= (r.get("stars") or 0) <= stars_max]
    info = _extract_company_info(jsonld, soup)
    return {"company_info": info, "company_reviews": reviews}


def _fetch_once(url: str, timeout: int = 20) -> tuple[str, Optional[str]]:
    """Один HTTP-запрос. Возвращает (html, error_or_None)."""
    try:
        with requests.Session() as s:
            s.headers.update(_HEADERS)
            resp = s.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return "", f"Сетевая ошибка: {exc}"
    if resp.status_code != 200:
        return "", f"HTTP {resp.status_code} от Яндекс Карт"
    if is_captcha_html(resp.text):
        return resp.text, "captcha"
    return resp.text, None


def fetch_reviews(org_id: str, *, stars_min: int = 1, stars_max: int = 5) -> dict:
    """Парсит `https://yandex.ru/maps/org/<id>/reviews/` через HTTP (fast-режим).

    Возвращает {company_info, company_reviews}. На капче ретрай через 30 сек.
    На непреодолимой ошибке — {error: "..."}.
    """
    url = f"https://yandex.ru/maps/org/{org_id}/reviews/"

    html, err = _fetch_once(url)
    if err == "captcha":
        log.info("captcha for org %s — ретрай через 30 сек", org_id)
        time.sleep(30 + random.uniform(0, 5))
        html, err = _fetch_once(url)
        if err == "captcha":
            return {"error": "Яндекс показал капчу (после ретрая)"}
    if err:
        return {"error": err}

    return parse_dom(html, stars_min=stars_min, stars_max=stars_max)


def throttle_between_orgs():
    """Пауза между организациями (fast-режим). 8-14 сек — снижает шанс капчи."""
    time.sleep(random.uniform(8, 14))


def throttle_between_orgs_deep():
    """Пауза между организациями в deep-режиме. Браузер тяжелее → пауза 15-25 сек."""
    time.sleep(random.uniform(15, 25))
