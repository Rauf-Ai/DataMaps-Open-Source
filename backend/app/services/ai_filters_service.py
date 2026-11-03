from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""AI-помощник: парсит свободный промпт пользователя → выставляет фильтры поиска.

Загружает дерево категорий из data/category_tree.json и список регионов из DuckDB,
формирует промт для LLM, получает JSON-ответ, валидирует (отбрасывает галлюцинации).
"""

import json
import logging
import os
import re
import time
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.db import get_duck
from app.services.llm_client import chat_json


log = logging.getLogger(__name__)
settings = get_settings()


_SYSTEM_PROMPT = """\
Ты помощник по поиску B2B-клиентов в базе российских организаций (Яндекс Карты).
Пользователь описывает кому он хочет продавать или какую задачу решает.
Твоя задача — рассуждать как опытный sales-консультант и выставить фильтры поиска.

═══════════════════════════════════════════════════════════════
ОБЩИЕ ПРИНЦИПЫ РАССУЖДЕНИЯ
═══════════════════════════════════════════════════════════════

ПРИНЦИП 1 — Категории через окупаемость продукта.
Подумай: каким бизнесам экономически выгодно покупать то, что предлагает
пользователь? Учитывай: средний чек у клиента, маржинальность ниши,
длину цикла сделки, бюджет на маркетинг/IT. Включай только те leaf-категории,
где это реалистично окупается. Исключай ниши, где продукт не приживётся.

ПРИНЦИП 2 — Контактные фильтры через ЛОГИКУ ПОТРЕБНОСТИ.
Главное правило: «что нужно клиенту, чтобы купить у пользователя — должно
БЫТЬ; что пользователь продаёт клиенту — у клиента должно ОТСУТСТВОВАТЬ».

Применяй системно:
- Пользователь продаёт САЙТЫ / разработку сайтов → клиент должен НЕ иметь
  сайта → has_website="no".
- Пользователь продаёт SEO/контекст/таргет/услуги по сайту → у клиента сайт
  ДОЛЖЕН быть → has_website="yes".
- Пользователь продаёт услуги по соцсети X (SMM, рилс, контент, таргет в X) →
  у клиента должен быть аккаунт в этой X → has_<X>="yes" для VK/Instagram/Telegram.
- Пользователь делает рассылки (email-маркетинг, холодные письма) → у клиента
  должен быть email → has_email="yes".
- Пользователь продаёт IP-телефонию или CRM с обзвонами → has_phone="yes".
- Пользователь сам ведёт телеграм-канал и ищет коллабы → has_telegram="yes".
- Пользователь ищет компании с профилем Max → has_max="yes".
- Холодный обзвон по телефону → has_phone="yes".
- Если задача нейтральная (анализ конкурентов, общий поиск) — оставь "any".

ПРИНЦИП 3 — Рейтинг и отзывы.
Только если пользователь явно сказал «топовые», «крупные», «надёжные»,
«с хорошей репутацией» — выставь reviews_min=10. Иначе 0/0/5.
Если пользователь сказал «слабые», «без отзывов», «новые» — reviews_max=5
или reviews_min=0.

ПРИНЦИП 4 — География.
- Если регион/город прямо назван (Москва, Чувашия, Чебоксары) — включи.
- Если города назвали без региона, но регион можно понять (Чебоксары →
  Чувашская Республика) — используй cities в формате "Регион|Город".
- ЕСЛИ ГЕОГРАФИЯ В ПРОМПТЕ НЕ УПОМЯНУТА ВООБЩЕ — оставь regions=[] и
  cities=[]. НЕ заполняй регионами «на всякий случай». Пустой список =
  поиск по всей России.

ПРИНЦИП 5 — Категории.
Выбирай ИМЕННО leaf-категории (самый нижний уровень в дереве), а не названия
top-разделов или подкатегорий. Например, не «Мебель» (sub), а «Кухонная
мебель», «Корпусная мебель» (leaf). Если задача общая — включай все
правдоподобные leaf'ы, но не больше 12 самых перспективных. Если узкая —
точечно 5-15 категорий.

ПРИНЦИП 6 — Скорость ответа.
Не рассуждай вслух и не перечисляй альтернативы. Быстро выбери наиболее
подходящие фильтры и верни компактный JSON. Поле reasoning — максимум
160 символов.

═══════════════════════════════════════════════════════════════
ФОРМАТ ОТВЕТА
═══════════════════════════════════════════════════════════════
Верни СТРОГО JSON со всеми полями:
{
  "categories": ["<leaf>", ...],
  "regions": ["<region>", ...],
  "cities": [],
  "has_phone": "any"|"yes"|"no",
  "has_website": "any"|"yes"|"no",
  "has_email": "any"|"yes"|"no",
  "has_vk": "any"|"yes"|"no",
  "has_instagram": "any"|"yes"|"no",
  "has_telegram": "any"|"yes"|"no",
  "has_max": "any"|"yes"|"no",
  "rating_min": 0,
  "rating_max": 5,
  "reviews_min": 0,
  "reasoning": "<2-3 предложения почему выбраны категории и фильтры — упомяни логику контактного фильтра, если он не any>"
}
Имена категорий и регионов копируй ТОЧНО как в списке."""


_DEFAULT_FILTERS = {
    "categories": [],
    "regions": [],
    "cities": [],
    "has_phone": "any",
    "has_website": "any",
    "has_email": "any",
    "has_vk": "any",
    "has_instagram": "any",
    "has_telegram": "any",
    "has_max": "any",
    "rating_min": 0,
    "rating_max": 5,
    "reviews_min": 0,
}

_TRI_VALUES = {"any", "yes", "no"}
_MAX_AI_CANDIDATE_CATEGORIES = 140
_MAX_SELECTED_CATEGORIES = 12

_INTENT_CATEGORY_TERMS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (
        ("1с", "1c", "erp", "crm", "учет", "учёт", "касс", "склад", "товароуч", "автоматизац"),
        (
            "магазин", "торгов", "склад", "опт", "постав", "дистриб", "кафе", "ресторан",
            "бар", "пекар", "кондитер", "доставка", "производ", "мебел", "стройматериал",
            "автозапчаст", "аптек", "маркет", "супермаркет", "оборудован",
        ),
    ),
    (
        ("сайт", "seo", "сео", "директ", "контекст", "продвиж", "лид", "заявк", "маркетинг", "реклам"),
        (
            "кухн", "мебел", "строител", "ремонт", "недвиж", "стоматолог", "клиник",
            "медицин", "косметолог", "юрид", "адвокат", "автосервис", "автосалон",
            "окн", "двер", "кондиционер", "натяжн", "кровл", "фасад", "банкет",
            "ресторан", "отель", "гостиниц", "турист", "образован", "курсы",
        ),
    ),
    (
        ("рилс", "reels", "инстаграм", "instagram", "smm", "смм", "контент", "блог"),
        (
            "косметолог", "салон", "студия", "фитнес", "ресторан", "кафе", "бар",
            "отель", "магазин", "одежд", "обув", "украшен", "цвет", "фото",
            "свад", "детск", "клиник", "стоматолог",
        ),
    ),
    (
        ("строител", "дорож", "асфальт", "спецтехник", "благоустрой", "бетон", "кровл", "фасад"),
        (
            "строител", "дорог", "асфальт", "ремонт", "проект", "инженер", "геодез",
            "спецтехник", "благоустрой", "бетон", "кровл", "фасад",
        ),
    ),
]

_STOP_WORDS = {
    "для", "мне", "мои", "мой", "кому", "кого", "что", "как", "или", "при", "над",
    "под", "без", "есть", "найди", "найти", "покажи", "хочу", "можно", "которые",
    "компании", "организации", "клиенты", "клиентов", "продажи", "продать",
}

_CITY_ALIASES: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("москв",), ("москва", "москв"), "Москва"),
    (("санкт петербург", "петербург", "спб"), ("санкт петербург",), "Санкт-Петербург"),
    (("казан",), ("татарстан",), "Казань"),
    (("чебоксар",), ("чуваш",), "Чебоксары"),
    (("ульяновск",), ("ульяновск",), "Ульяновск"),
    (("екатеринбург",), ("свердловск",), "Екатеринбург"),
    (("новосибирск",), ("новосибирск",), "Новосибирск"),
    (("краснодар",), ("краснодар",), "Краснодар"),
)

_REGION_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("татарстан",), "Республика Татарстан"),
    (("чуваш",), "Чувашская Республика"),
    (("московск област", "подмосков"), "Московская область"),
    (("краснодарск кра",), "Краснодарский край"),
)

_LIVE_QUERY_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ресторан",), "Рестораны"),
    (("столов",), "Столовые"),
    (("кафе",), "Кафе"),
    (("автосервис",), "Автосервисы"),
    (("стоматолог",), "Стоматологии"),
    (("салон красот",), "Салоны красоты"),
    (("фитнес",), "Фитнес-клубы"),
    (("кухн", "мебел на заказ"), "Кухни на заказ"),
)

_FALLBACK_FILTER_WORDS = {
    "сайт", "сайта", "телефон", "рейтинг", "рейтингом", "отзыв", "отзывов",
    "высоким", "хорошим", "много", "немного", "которых", "которые", "кого",
}


@lru_cache(maxsize=1)
def _tree_path() -> Path:
    return Path(os.path.dirname(settings.duckdb_path)) / "category_tree.json"


@lru_cache(maxsize=1)
def load_category_tree() -> list[dict]:
    p = _tree_path()
    if not p.exists():
        log.warning("category_tree.json не найден: %s", p)
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("category_tree.json повреждён: %s", exc)
        return []


_regions_cache: dict[str, Any] = {"ts": 0.0, "items": []}


def load_regions(ttl: int = 300) -> list[str]:
    """Кэшированный список регионов из DuckDB. Освежается каждые 5 мин."""
    now = time.time()
    if _regions_cache["items"] and now - _regions_cache["ts"] < ttl:
        return _regions_cache["items"]
    try:
        with get_duck() as cur:
            rows = cur.execute("SELECT region FROM facets_regions ORDER BY region").fetchall()
        items = [r[0] for r in rows if r and r[0]]
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось загрузить регионы: %s", exc)
        items = []
    _regions_cache.update(ts=now, items=items)
    return items


def _compact_tree_text(tree: list[dict]) -> str:
    """Превращает дерево в компактный текст: top → sub → leaf-категории через запятую."""
    lines: list[str] = []
    for top in tree:
        lines.append(f"## {top.get('name', '')}")
        for sub in top.get("subcategories") or []:
            cats = sub.get("categories") or []
            if not cats:
                continue
            cats_str = ", ".join(cats)
            lines.append(f"- {sub.get('name', '')}: {cats_str}")
    return "\n".join(lines)


def _norm(s: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", s.lower().replace("ё", "е")).strip()


def _prompt_terms(prompt: str) -> set[str]:
    words = set()
    for w in _norm(prompt).split():
        if len(w) >= 4 and w not in _STOP_WORDS:
            words.add(w)
            # Небольшой стемминг без внешних зависимостей: для поиска по названию
            # категории достаточно общей основы слова.
            if len(w) >= 7:
                words.add(w[:6])
    text = _norm(prompt)
    for triggers, terms in _INTENT_CATEGORY_TERMS:
        if any(t in text for t in triggers):
            words.update(terms)
    return words


def _candidate_tree_for_prompt(prompt: str, tree: list[dict]) -> list[dict]:
    """Сужает каталог категорий перед отправкой в LLM.

    Полный каталог на 1554 категории заставлял reasoning-модели читать слишком
    большой контекст и иногда упираться в LLM_TIMEOUT. Здесь оставляем только
    категории, которые совпали со словами пользователя или с интентными
    синонимами (сайт/1С/SMM/конкуренты и т.п.).
    """
    terms = _prompt_terms(prompt)
    if not terms:
        return tree

    scored: list[tuple[int, str, str, str]] = []
    seen: set[str] = set()
    for top in tree:
        top_name = top.get("name", "")
        for sub in top.get("subcategories") or []:
            sub_name = sub.get("name", "")
            for cat in sub.get("categories") or []:
                if not cat or cat in seen:
                    continue
                hay = _norm(f"{top_name} {sub_name} {cat}")
                score = sum(1 for t in terms if t in hay)
                if score:
                    scored.append((score, top_name, sub_name, cat))
                    seen.add(cat)

    if not scored:
        return tree

    scored.sort(key=lambda x: (-x[0], x[3]))
    selected = scored[:_MAX_AI_CANDIDATE_CATEGORIES]
    grouped: dict[str, dict[str, list[str]]] = {}
    for _, top_name, sub_name, cat in selected:
        grouped.setdefault(top_name, {}).setdefault(sub_name, []).append(cat)

    out = []
    for top_name, subs in grouped.items():
        subitems = []
        total = 0
        for sub_name, cats in subs.items():
            cats_sorted = sorted(cats)
            subitems.append({"name": sub_name, "count": len(cats_sorted), "categories": cats_sorted})
            total += len(cats_sorted)
        out.append({"name": top_name, "count": total, "subcategories": subitems})
    return out


def _flatten_categories(tree: list[dict]) -> set[str]:
    """Все валидные leaf-категории из дерева."""
    out: set[str] = set()
    for top in tree:
        for sub in top.get("subcategories") or []:
            for c in sub.get("categories") or []:
                if c:
                    out.add(c)
    return out


def _coerce_tri(v: Any) -> str:
    if isinstance(v, str) and v.lower() in _TRI_VALUES:
        return v.lower()
    return "any"


def _coerce_float(v: Any, default: float, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


def _coerce_int(v: Any, default: int = 0) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def _apply_contact_intent(prompt: str, filters: dict) -> None:
    text = _norm(prompt)
    explicit_site = False
    if any(t in text for t in ("без сайта", "нет сайта", "не имеют сайта")):
        filters["has_website"] = "no"
        explicit_site = True
    elif any(t in text for t in ("с сайтом", "есть сайт", "имеют сайт")):
        filters["has_website"] = "yes"
        explicit_site = True
    elif any(t in text for t in ("сайт", "лендинг", "разработк сайта", "создан сайта")):
        filters["has_website"] = "no"
        explicit_site = True
    elif any(t in text for t in ("seo", "сео", "директ", "контекст", "продвиж", "реклам")):
        filters["has_website"] = "yes"
        explicit_site = True

    if not explicit_site and any(t in text for t in ("1с", "1c", "erp", "crm", "учет", "учет", "касс", "склад")):
        filters["has_website"] = "any"

    if any(t in text for t in ("рилс", "reels", "инстаграм", "instagram")):
        filters["has_instagram"] = "yes"
    if any(t in text for t in ("telegram", "телеграм", "тг канал", "тг-канал")):
        filters["has_telegram"] = "yes"
    if {"max", "макс"} & set(text.split()):
        filters["has_max"] = "yes"
    if any(t in text for t in ("email", "емейл", "почт", "рассылк")):
        filters["has_email"] = "yes"
    if any(t in text for t in ("обзвон", "звон", "телефон", "whatsapp", "ватсап")):
        filters["has_phone"] = "yes"


def _validate(raw: dict, tree: list[dict], regions: list[str], prompt: str = "") -> tuple[dict, dict, str]:
    """Возвращает (filters, unknown, reasoning).

    unknown — словарь с отброшенными названиями (для UI-предупреждения и логов).
    """
    valid_cats = _flatten_categories(tree)
    valid_regions = set(regions)

    proposed_cats = raw.get("categories") or []
    if not isinstance(proposed_cats, list):
        proposed_cats = []
    kept_cats = [c for c in proposed_cats if isinstance(c, str) and c in valid_cats][:_MAX_SELECTED_CATEGORIES]
    unknown_cats = [c for c in proposed_cats if isinstance(c, str) and c not in valid_cats]

    proposed_regs = raw.get("regions") or []
    if not isinstance(proposed_regs, list):
        proposed_regs = []
    kept_regs = [r for r in proposed_regs if isinstance(r, str) and r in valid_regions]
    unknown_regs = [r for r in proposed_regs if isinstance(r, str) and r not in valid_regions]

    proposed_cities = raw.get("cities") or []
    if not isinstance(proposed_cities, list):
        proposed_cities = []
    # Города — формат "Регион|Город". Регион должен быть в valid_regions.
    kept_cities = []
    for c in proposed_cities:
        if not isinstance(c, str) or "|" not in c:
            continue
        reg, _, town = c.partition("|")
        if reg in valid_regions and town:
            kept_cities.append(c)

    filters = dict(_DEFAULT_FILTERS)
    filters["categories"] = kept_cats
    filters["regions"] = kept_regs
    filters["cities"] = kept_cities
    filters["has_phone"] = _coerce_tri(raw.get("has_phone"))
    filters["has_website"] = _coerce_tri(raw.get("has_website"))
    filters["has_email"] = _coerce_tri(raw.get("has_email"))
    filters["has_vk"] = _coerce_tri(raw.get("has_vk"))
    filters["has_instagram"] = _coerce_tri(raw.get("has_instagram"))
    filters["has_telegram"] = _coerce_tri(raw.get("has_telegram"))
    filters["has_max"] = _coerce_tri(raw.get("has_max"))
    filters["rating_min"] = _coerce_float(raw.get("rating_min"), 0.0, 0.0, 5.0)
    filters["rating_max"] = _coerce_float(raw.get("rating_max"), 5.0, 0.0, 5.0)
    if filters["rating_min"] > filters["rating_max"]:
        filters["rating_min"], filters["rating_max"] = 0.0, 5.0
    filters["reviews_min"] = _coerce_int(raw.get("reviews_min"), 0)
    _apply_contact_intent(prompt, filters)

    reasoning = raw.get("reasoning") or ""
    if not isinstance(reasoning, str):
        reasoning = str(reasoning)

    return filters, {"categories": unknown_cats, "regions": unknown_regs}, reasoning[:600]


def fallback_filters_from_prompt(prompt: str, tree: list[dict], regions: list[str]) -> dict:
    """Детерминированный разбор для локальной разработки без LLM-ключа."""
    text = _norm(prompt)
    words = [
        word for word in text.split()
        if len(word) >= 4 and word not in _STOP_WORDS and word not in _FALLBACK_FILTER_WORDS
    ]
    terms = set(words)
    terms.update(word[:6] for word in words if len(word) >= 7)
    scored: list[tuple[int, str]] = []
    for top in tree:
        top_name = _norm(top.get("name", ""))
        for sub in top.get("subcategories") or []:
            sub_name = _norm(sub.get("name", ""))
            for category in sub.get("categories") or []:
                haystack = _norm(f"{top_name} {sub_name} {category}")
                score = sum(1 for term in terms if term in haystack)
                if score:
                    scored.append((score, category))

    categories: list[str] = []
    if scored:
        best = max(score for score, _ in scored)
        categories = list(dict.fromkeys(
            category for score, category in sorted(scored, key=lambda item: (-item[0], item[1]))
            if score == best
        ))[:_MAX_SELECTED_CATEGORIES]
    if not categories:
        categories = [
            query for aliases, query in _LIVE_QUERY_ALIASES
            if any(alias in text for alias in aliases)
        ]
    if not categories:
        cleaned = re.sub(
            r"^(?:найди|найти|покажи|подбери|собери|выгрузи)\s+(?:мне\s+)?(?:\d+\s+)?",
            "",
            prompt.strip(),
            flags=re.IGNORECASE,
        )
        cleaned = re.split(
            r"\s+(?:в|по)\s+[А-ЯЁ]|\s+(?:у кого|которые|которых|без|с рейтинг|с отзыв)",
            cleaned,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" ,.-")
        categories = [cleaned[:120]] if cleaned else []

    valid_regions = set(regions)
    selected_regions: list[str] = []
    selected_cities: list[str] = []
    for aliases, region_terms, city in _CITY_ALIASES:
        if not any(alias in text for alias in aliases):
            continue
        region = next(
            (
                item for item in regions
                if any(term == _norm(item) for term in region_terms)
            ),
            None,
        )
        if region is None:
            region = next(
                (
                    item for item in regions
                    if any(term in _norm(item) for term in region_terms)
                ),
                None,
            )
        if region:
            selected_cities.append(f"{region}|{city}")
    if not selected_cities:
        for aliases, region in _REGION_ALIASES:
            if any(alias in text for alias in aliases) and region in valid_regions:
                selected_regions.append(region)
        for region in regions:
            if _norm(region) and _norm(region) in text:
                selected_regions.append(region)

    raw = dict(_DEFAULT_FILTERS)
    raw.update(
        categories=categories,
        regions=list(dict.fromkeys(selected_regions)),
        cities=list(dict.fromkeys(selected_cities)),
        reasoning="Применён резервный разбор запроса: проверьте предложенные параметры перед поиском.",
    )
    if "рейтинг" in text and any(term in text for term in ("высок", "отличн", "топов")):
        raw["rating_min"] = 4.5
    elif "рейтинг" in text and any(term in text for term in ("хорош", "выше")):
        raw["rating_min"] = 4.0
    rating_match = re.search(r"рейтинг\D{0,18}(?:от|выше)\s*(\d(?:[.,]\d+)?)", prompt, re.IGNORECASE)
    if rating_match:
        raw["rating_min"] = float(rating_match.group(1).replace(",", "."))

    if any(term in text for term in ("много отзыв", "популярн", "много оцен")):
        raw["reviews_min"] = 50
    reviews_match = re.search(r"(?:от|больше|более)\s*(\d+)\s*(?:отзыв|оцен)", prompt, re.IGNORECASE)
    if reviews_match:
        raw["reviews_min"] = int(reviews_match.group(1))

    filters, unknown, reasoning = _validate(raw, tree, regions, prompt)
    # Live-парсер принимает свободный поисковый запрос. Локальное дерево может
    # быть урезанным, поэтому резервный режим не должен отбрасывать этот запрос.
    filters["categories"] = categories
    unknown["categories"] = []
    return {"filters": filters, "reasoning": reasoning, "unknown": unknown}


async def parse_filters_from_prompt(prompt: str) -> dict:
    """Возвращает {filters, reasoning, unknown}. На ошибке LLM поднимает RuntimeError."""
    tree = load_category_tree()
    regions = load_regions()
    if not tree:
        raise RuntimeError("Дерево категорий не загружено — нечего предлагать AI")
    candidate_tree = _candidate_tree_for_prompt(prompt, tree)
    candidate_count = len(_flatten_categories(candidate_tree))
    log.info("AI filters category candidates: %d", candidate_count)

    user_msg = (
        f"Задача пользователя: {prompt}\n\n"
        f"Доступные регионы ({len(regions)} шт):\n"
        + ", ".join(regions)
        + f"\n\nКандидаты категорий ({candidate_count} leaf из полного каталога). "
          "Выбирай только из этого списка:\n"
        + _compact_tree_text(candidate_tree)
    )
    raw, err = await chat_json(
        _SYSTEM_PROMPT,
        user_msg,
        temperature=0.15,
        x_title="DataMaps AI-filters",
        max_tokens=2500,
        fast=True,
    )
    if raw is None and err == "LLM_API_KEY не настроен":
        log.warning("LLM_API_KEY не настроен; используется локальный резервный разбор")
        return fallback_filters_from_prompt(prompt, candidate_tree, regions)
    if raw is None:
        raise RuntimeError(err or "LLM не ответил")

    filters, unknown, reasoning = _validate(raw, tree, regions, prompt)
    return {"filters": filters, "reasoning": reasoning, "unknown": unknown}
