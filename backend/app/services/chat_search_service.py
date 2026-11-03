from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import json
import math
import re

from app.services.ai_filters_service import fallback_filters_from_prompt, load_category_tree, load_regions
from app.services.llm_client import chat_json
from app.services.maps.normalization import locale_for_country


DEFAULT_LIVE_PLAN = {
    "searches": [],
    "source": "yandex",
    "max": 50,
    "target_count": 50,
    "f_phone": "any",
    "f_site": "any",
    "f_franchise": "any",
    "social_nets": [],
    "reviews_min": 0,
    "reviews_max": None,
    "rating_min": 0.0,
    "rating_max": 5.0,
}

_LIVE_PLAN_PROMPT = """\
Ты планировщик live-поиска организаций в Яндекс Картах и Google Maps.
Преобразуй диалог пользователя в полный редактируемый план поиска.

Правила:
1. Для КАЖДОЙ указанной локации создай от 3 до 5 разных, но релевантных поисковых
   формулировок. Формулировки должны расширять покрытие, а не быть перестановкой слов.
   Локации с одинаковыми формулировками обязательно объединяй в одну search_group,
   чтобы ответ оставался компактным даже для десятков городов.
2. Не ограничивайся старым справочником категорий: это запросы прямо к картам.
3. Россия и Узбекистан всегда source="yandex". Любая другая страна source="google".
4. target_count — итоговое число уникальных организаций во всём результате.
5. Если пользователь продаёт сайт, ищи организации без сайта. Если продаёт SEO или
   рекламу для сайта — с сайтом. Контактные и социальные фильтры выбирай из логики
   задачи. Не добавляй ограничение, которое не улучшает целевой результат.
6. Если это уточнение, сохрани все критерии текущего плана, которые пользователь не
   просил изменить.
7. Не показывай рассуждения. Верни только JSON.
8. В queries указывай только вид бизнеса или поисковое намерение БЕЗ города,
   региона и страны. Всю географию указывай исключительно в locations.
9. Если страна не русскоязычная, формулируй КАЖДЫЙ queries на основном местном
   языке страны: США — English, Индонезия — Bahasa Indonesia, Китай — 中文. Не
   оставляй русский вариант для Google Maps; пользовательский русский запрос
   переводи в локальную поисковую формулировку.

Формат:
{
  "search_groups": [
    {
      "queries":["формулировка 1","формулировка 2","формулировка 3"],
      "locations":["город 1","город 2"],
      "country":"Россия",
      "source":"yandex|google"
    }
  ],
  "target_count": 50,
  "f_phone":"any|yes|no",
  "f_site":"any|yes|no",
  "f_franchise":"any|yes|no",
  "social_nets":["vk|instagram|telegram|max"],
  "reviews_min":0,
  "reviews_max":null,
  "rating_min":0,
  "rating_max":5,
  "reasoning":"Кратко объясни выбранные направления и фильтры, без chain-of-thought"
}
"""

_SOCIAL_FIELDS = {
    "has_vk": ("vk", ("вконтакте", "вк", "vk")),
    "has_instagram": ("instagram", ("инстаграм", "instagram", "рилс", "reels")),
    "has_telegram": ("telegram", ("телеграм", "telegram", "тг")),
    "has_max": ("max", ("max", "макс")),
}

_ADDITIVE_FOLLOW_UP_TERMS = ("добавь", "дополни", "ещё", "еще")
_SOCIAL_NETWORK_ALIASES = {
    "vk": "vk",
    "вк": "vk",
    "вконтакте": "vk",
    "instagram": "instagram",
    "инстаграм": "instagram",
    "telegram": "telegram",
    "телеграм": "telegram",
    "тг": "telegram",
    "max": "max",
    "макс": "max",
}
_SOCIAL_NETWORK_TOKEN = r"vk|вк|вконтакте|instagram|инстаграм|telegram|телеграм|тг|max|макс"
_SOCIAL_REMOVAL_PATTERN = re.compile(
    r"(?<![a-zа-яё0-9_])(?:убери|удали|исключи|без)(?![a-zа-яё0-9_])\s+"
    r"(?:(?:из\s+)?(?:фильтр[a-zа-яё]*|соцсет[a-zа-яё]*|сети|аккаунт[a-zа-яё]*|профил[a-zа-яё]*)\s+)?"
    rf"(?P<network>{_SOCIAL_NETWORK_TOKEN})(?![a-zа-яё0-9_])",
    re.IGNORECASE,
)
_SOCIAL_ONLY_PATTERN = re.compile(
    r"(?<![a-zа-яё0-9_])только(?![a-zа-яё0-9_])\s+"
    r"(?:с\s+)?"
    r"(?:(?:фильтр[a-zа-яё]*|соцсет[a-zа-яё]*|сети|аккаунт[a-zа-яё]*|профил[a-zа-яё]*)\s+)?"
    rf"(?P<networks>(?:{_SOCIAL_NETWORK_TOKEN})(?:\s*(?:,|и)\s*(?:{_SOCIAL_NETWORK_TOKEN}))*)"
    r"(?![a-zа-яё0-9_])",
    re.IGNORECASE,
)
_SOCIAL_NETWORK_PATTERN = re.compile(
    rf"(?<![a-zа-яё0-9_])(?P<network>{_SOCIAL_NETWORK_TOKEN})(?![a-zа-яё0-9_])",
    re.IGNORECASE,
)


def default_live_plan() -> dict:
    return {**DEFAULT_LIVE_PLAN, "searches": [], "social_nets": []}


def _country_source(country: str, location: str = "") -> str:
    value = _text(country or location).lower().replace("ё", "е")
    yandex_markers = (
        "россия", "российская федерация", "russia", "узбекистан", "uzbekistan",
        "москва", "казань", "ташкент", "самарканд", "бухара",
    )
    return "yandex" if any(marker in value for marker in yandex_markers) else "google"


def _normalize_searches(raw_rows: Any, previous_rows: list[dict]) -> list[dict]:
    rows = raw_rows if isinstance(raw_rows, list) and raw_rows else previous_rows
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    per_location: dict[str, int] = {}
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        location = _text(raw.get("location"))
        query = _query_without_location(_text(raw.get("query")), location)
        if not query or not location:
            continue
        country = _text(raw.get("country")) or ("Россия" if _country_source("", location) == "yandex" else location)
        language, google_locale = locale_for_country(country)
        key = (query.casefold(), location.casefold())
        if key in seen:
            continue
        location_key = location.casefold()
        if per_location.get(location_key, 0) >= 5:
            continue
        seen.add(key)
        per_location[location_key] = per_location.get(location_key, 0) + 1
        out.append({
            "query": query[:160],
            "location": location[:160],
            "country": country[:100],
            "source": _country_source(country, location),
            "language": language,
            "google_locale": google_locale,
        })
    return out[:120]


def _query_without_location(query: str, location: str) -> str:
    """Keep map query and geography in separate fields even if the LLM mixes them."""
    query = _text(query)
    location = _text(location)
    if not query or not location:
        return query
    cleaned = re.sub(re.escape(location), " ", query, flags=re.IGNORECASE)
    # Russian place names are commonly inflected in prose: Москва -> Москве,
    # Казань -> Казани. Remove significant location stems as well.
    location_words = re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", location)
    ignored = {"город", "область", "район", "республика", "край", "страна"}
    for word in location_words:
        if word.casefold().replace("ё", "е") in ignored:
            continue
        stem_len = max(4, len(word) - (2 if len(word) >= 7 else 1))
        stem = re.escape(word[:stem_len])
        cleaned = re.sub(
            rf"\b(?:в|во|по|из|для)\s+{stem}[A-Za-zА-Яа-яЁё-]*\b|\b{stem}[A-Za-zА-Яа-яЁё-]*\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:—–-")
    return cleaned or "организации"


def _expand_search_groups(raw_groups: Any) -> list[dict]:
    """Expand compact LLM groups into parser-ready query/location rows."""
    if not isinstance(raw_groups, list):
        return []
    rows: list[dict] = []
    for group in raw_groups:
        if not isinstance(group, dict):
            continue
        queries = group.get("queries")
        locations = group.get("locations")
        if not isinstance(queries, list) or not isinstance(locations, list):
            continue
        country = _text(group.get("country"))
        source = _text(group.get("source"))
        for location in locations[:30]:
            for query in queries[:5]:
                rows.append({
                    "query": query,
                    "location": location,
                    "country": country,
                    "source": source,
                })
    return rows


def _valid_ai_search_groups(raw: dict) -> bool:
    groups = raw.get("search_groups")
    if isinstance(groups, list) and groups:
        return all(
            isinstance(group, dict)
            and isinstance(group.get("locations"), list)
            and bool(group.get("locations"))
            and isinstance(group.get("queries"), list)
            and 3 <= len(group.get("queries")) <= 5
            for group in groups
        )

    rows = raw.get("searches") or []
    counts: dict[str, int] = {}
    for row in rows:
        key = _text(row.get("location")).casefold()
        if key:
            counts[key] = counts.get(key, 0) + 1
    return bool(counts) and all(3 <= count <= 5 for count in counts.values())


def _expected_results_per_search(plan: dict) -> int:
    """Conservative estimate of usable unique cards produced by one map query."""
    expected = 12.0
    if _tri(plan.get("f_site")) == "no":
        expected *= 0.5
    elif _tri(plan.get("f_site")) == "yes":
        expected *= 0.8
    if _tri(plan.get("f_phone")) == "no":
        expected *= 0.45
    elif _tri(plan.get("f_phone")) == "yes":
        expected *= 0.8
    if _tri(plan.get("f_franchise")) != "any":
        expected *= 0.65
    for _network in plan.get("social_nets") or []:
        expected *= 0.7

    reviews_min = _int(plan.get("reviews_min"), 0)
    if reviews_min >= 100:
        expected *= 0.45
    elif reviews_min >= 50:
        expected *= 0.6
    elif reviews_min >= 10:
        expected *= 0.8

    rating_min = _float(plan.get("rating_min"), 0.0)
    if rating_min >= 4.5:
        expected *= 0.65
    elif rating_min >= 4.0:
        expected *= 0.8
    return max(1, round(expected))


def _budget_searches(rows: list[dict], plan: dict, max_rows: int = 50) -> list[dict]:
    """Select a volume-aware, geographically balanced subset of LLM variants."""
    if not rows:
        return []
    by_location: dict[str, list[dict]] = {}
    for row in rows:
        location = _text(row.get("location"))
        if not location:
            continue
        by_location.setdefault(location, []).append(row)
    if not by_location:
        return rows[:max_rows]

    target_count = max(1, _int(plan.get("target_count") or plan.get("max"), 50))
    expected = _expected_results_per_search(plan)
    variants = math.ceil(target_count / (len(by_location) * expected))
    variants = max(1, min(5, variants))

    # Round-robin keeps the parser from exhausting the target in the first city.
    selected: list[dict] = []
    for variant_index in range(variants):
        for location_rows in by_location.values():
            if variant_index < len(location_rows):
                selected.append(location_rows[variant_index])
                if len(selected) >= max_rows:
                    return selected
    return selected


def normalize_live_ai_response(raw: dict, previous: dict, latest_message: str) -> dict:
    """Validate an LLM live-search plan and deterministically correct routing."""
    plan = {**default_live_plan(), **(previous or {})}
    previous_rows = list(plan.get("searches") or [])
    compact_rows = _expand_search_groups(raw.get("search_groups"))
    rows = _normalize_searches(compact_rows or raw.get("searches"), previous_rows)
    if not rows and isinstance(raw.get("filters"), dict):
        legacy = merge_live_plan(raw, previous, latest_message)
        rows = _normalize_searches(legacy.get("searches"), previous_rows)
        plan.update(legacy)
    plan["searches"] = rows

    explicit = _explicit_limit(latest_message)
    target = _int(raw.get("target_count"), 0) or explicit or _int(
        plan.get("target_count") or plan.get("max"), 50
    )
    plan["target_count"] = max(1, min(5000, target))
    plan["max"] = plan["target_count"]

    for key in ("f_phone", "f_site", "f_franchise"):
        if key in raw:
            plan[key] = _tri(raw.get(key))
    socials = raw.get("social_nets")
    if isinstance(socials, list):
        normalized_socials = {
            _text(value).lower() for value in socials
            if _text(value).lower() in {"vk", "instagram", "telegram", "max"}
        }
    else:
        normalized_socials = set(plan.get("social_nets") or [])
    plan["social_nets"] = sorted(_resolve_explicit_social_intent(
        normalized_socials,
        plan.get("social_nets") or [],
        latest_message,
    ))
    if "reviews_min" in raw:
        plan["reviews_min"] = _int(raw.get("reviews_min"), 0)
    if "reviews_max" in raw:
        value = raw.get("reviews_max")
        plan["reviews_max"] = _int(value) if value not in (None, "", 0) else None
    if "rating_min" in raw:
        plan["rating_min"] = _float(raw.get("rating_min"), 0.0)
    if "rating_max" in raw:
        plan["rating_max"] = max(plan["rating_min"], _float(raw.get("rating_max"), 5.0))
    plan["searches"] = _budget_searches(rows, plan)
    plan["source"] = plan["searches"][0]["source"] if plan["searches"] else _text(plan.get("source")) or "yandex"
    return plan


def fallback_live_plan(latest_message: str, previous: dict) -> dict:
    """Emergency keyword parser. It deliberately sees only the latest message."""
    fallback = fallback_filters_from_prompt(latest_message, load_category_tree(), load_regions())
    plan = merge_live_plan(fallback, previous, latest_message)
    rows = _normalize_searches(plan.get("searches"), [])
    plan["searches"] = rows
    plan["target_count"] = _explicit_limit(latest_message) or _int(
        previous.get("target_count") or previous.get("max"), 50
    )
    plan["max"] = plan["target_count"]
    return plan


def directions_label(count: int) -> str:
    remainder_100 = count % 100
    remainder_10 = count % 10
    if remainder_10 == 1 and remainder_100 != 11:
        noun = "поисковое направление"
    elif remainder_10 in {2, 3, 4} and remainder_100 not in {12, 13, 14}:
        noun = "поисковых направления"
    else:
        noun = "поисковых направлений"
    return f"{count} {noun}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mentioned(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


def _mentions_max(text: str) -> bool:
    return bool(re.search(r"(?<![a-zа-яё0-9_])(?:max|макс)(?![a-zа-яё0-9_])", text, re.IGNORECASE))


def _is_additive_social_follow_up(message: str) -> bool:
    return _mentioned(message, _ADDITIVE_FOLLOW_UP_TERMS)


def _explicit_social_removals(message: str) -> set[str]:
    return {
        _SOCIAL_NETWORK_ALIASES[match.group("network").casefold()]
        for match in _SOCIAL_REMOVAL_PATTERN.finditer(message)
    }


def _explicit_social_constraint(message: str) -> Optional[set[str]]:
    match = _SOCIAL_ONLY_PATTERN.search(message)
    if not match:
        return None
    return {
        _SOCIAL_NETWORK_ALIASES[network.group("network").casefold()]
        for network in _SOCIAL_NETWORK_PATTERN.finditer(match.group("networks"))
    }


def _resolve_explicit_social_intent(
    socials: set[str],
    previous_socials: list[str],
    message: str,
) -> set[str]:
    only_socials = _explicit_social_constraint(message)
    if only_socials is not None:
        return only_socials

    resolved = set(socials)
    removed_socials = _explicit_social_removals(message)
    if _is_additive_social_follow_up(message) or removed_socials:
        resolved.update(previous_socials)
    resolved.difference_update(removed_socials)
    return resolved


def _tri(value: Any) -> str:
    normalized = _text(value).lower()
    return normalized if normalized in {"any", "yes", "no"} else "any"


def _int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(5.0, float(value)))
    except (TypeError, ValueError):
        return default


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _fallback_query(message: str) -> str:
    cleaned = re.sub(
        r"^(найди|найти|покажи|подбери|собери|выгрузи)\s+",
        "",
        message.strip(),
        flags=re.IGNORECASE,
    )
    cleaned = re.split(r"\b(?:в|по)\s+[А-Яа-яЁё-]{3,}", cleaned, maxsplit=1)[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    return (cleaned or message.strip())[:120]


def _explicit_limit(message: str) -> Optional[int]:
    match = re.search(
        r"\b(?:найди|найти|покажи|подбери|собери|выгрузи)\s+(?:мне\s+)?(\d{1,5})\b",
        message,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return max(1, min(5000, int(match.group(1))))


def _locations(raw: dict) -> list[str]:
    cities = [_text(value).split("|")[-1].strip() for value in raw.get("cities") or []]
    regions = [_text(value) for value in raw.get("regions") or []]
    return _unique(cities + regions)


def merge_live_plan(ai_result: dict, previous: dict, latest_message: str) -> dict:
    raw = ai_result.get("filters") or {}
    had_plan = bool((previous or {}).get("searches"))
    plan = {**default_live_plan(), **(previous or {})}
    plan["social_nets"] = list(plan.get("social_nets") or [])
    old_rows = plan.get("searches") or []
    old_queries = _unique([_text(row.get("query")) for row in old_rows])
    old_locations = _unique([_text(row.get("location")) for row in old_rows])

    proposed_queries = _unique([_text(value) for value in raw.get("categories") or []])
    if proposed_queries and _mentioned(latest_message, ("добавь", "дополни", "ещё", "еще")):
        queries = _unique(old_queries + proposed_queries)
    else:
        queries = proposed_queries or old_queries
    if not queries:
        queries = [_fallback_query(latest_message)]

    proposed_locations = _locations(raw)
    all_russia = _mentioned(
        latest_message,
        ("вся россия", "по всей россии", "без региона", "география не важна"),
    )
    locations = ["Россия"] if all_russia else (proposed_locations or old_locations or ["Россия"])
    plan["searches"] = [
        {"query": query, "location": location}
        for query in queries[:12]
        for location in locations[:8]
    ][:20]

    contact_fields = {
        "has_phone": ("f_phone", ("телефон", "обзвон", "звон", "контакт")),
        "has_website": ("f_site", ("сайт", "seo", "сео", "директ", "продвиж")),
    }
    for source_key, (target_key, terms) in contact_fields.items():
        value = _tri(raw.get(source_key))
        if not had_plan or value in {"yes", "no"} or _mentioned(latest_message, terms):
            plan[target_key] = value

    socials = set(plan.get("social_nets") or [])
    for source_key, (network, terms) in _SOCIAL_FIELDS.items():
        value = _tri(raw.get(source_key))
        mentioned = _mentions_max(latest_message) if network == "max" else _mentioned(latest_message, terms)
        if not had_plan or mentioned:
            if value == "yes":
                socials.add(network)
            else:
                socials.discard(network)
    plan["social_nets"] = sorted(_resolve_explicit_social_intent(
        socials,
        plan.get("social_nets") or [],
        latest_message,
    ))

    if not had_plan or _mentioned(latest_message, ("отзыв", "популяр", "известн")):
        plan["reviews_min"] = _int(raw.get("reviews_min"), 0)
        reviews_max = raw.get("reviews_max")
        plan["reviews_max"] = _int(reviews_max) if reviews_max not in (None, "", 0) else None
    if not had_plan or _mentioned(latest_message, ("рейтинг", "звезд", "звёзд")):
        plan["rating_min"] = _float(raw.get("rating_min"), 0.0)
        plan["rating_max"] = max(
            plan["rating_min"],
            _float(raw.get("rating_max"), 5.0),
        )

    plan["source"] = plan.get("source") if plan.get("source") in {"yandex", "google"} else "yandex"
    requested_limit = _explicit_limit(latest_message)
    plan["max"] = requested_limit or max(1, min(5000, _int(plan.get("max"), 50) or 50))
    plan["f_franchise"] = _tri(plan.get("f_franchise"))
    return plan


def _context_prompt(messages: list[dict], previous: dict) -> str:
    history = "\n".join(
        f"{message.get('role', 'user')}: {_text(message.get('content'))}"
        for message in messages[-12:]
    )
    return (
        "Это диалог уточнения одного поиска. Верни полный актуальный набор фильтров. "
        "Критерии, которые пользователь не изменял последним сообщением, сохрани.\n"
        f"Текущий live-план: {json.dumps(previous or {}, ensure_ascii=False)}\n"
        f"Диалог:\n{history}"
    )[-7000:]


async def refine_live_plan(messages: list[dict], previous: dict) -> dict:
    if not messages:
        raise ValueError("Диалог пуст")
    latest_message = _text(messages[-1].get("content"))
    context = _context_prompt(messages, previous)
    raw, error = await chat_json(
        _LIVE_PLAN_PROMPT,
        context,
        temperature=0.2,
        x_title="DataMaps Live Search Planner",
        max_tokens=2200,
        fast=True,
    )
    normalized = normalize_live_ai_response(raw or {}, previous, latest_message) if raw is not None else None
    if raw is not None and not _valid_ai_search_groups(raw):
        error = "LLM вернул неполный план: требуется 3–5 формулировок на каждую локацию"
        raw = None
        normalized = None
    fallback_used = raw is None
    if fallback_used:
        plan = fallback_live_plan(latest_message, previous)
        reasoning = "Применён резервный разбор запроса: проверьте параметры перед поиском."
    else:
        plan = normalized or normalize_live_ai_response(raw or {}, previous, latest_message)
        reasoning = _text((raw or {}).get("reasoning")) or "Параметры поиска обновлены."
    count = len(plan["searches"])
    if fallback_used:
        assistant_message = (
            f"LLM временно недоступен. Резервный разбор подготовил {directions_label(count)}. "
            "Проверьте параметры перед поиском."
        )
    else:
        assistant_message = f"Подобрал {directions_label(count)}. {reasoning}"
    return {
        "plan": plan,
        "reasoning": reasoning,
        "assistant_message": assistant_message,
        "unknown": {},
        "fallback_used": fallback_used,
        "llm_error": error if fallback_used else None,
    }
