from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""SQL-конструктор для поиска организаций в DuckDB.

Все параметры передаются через positional placeholders (?) — никаких f-string склеек со значениями.
"""

from app.schemas.search import SearchFilters


SELECT_COLS = (
    "id, name, COALESCE(source_category, '') AS category, "
    "COALESCE(region, '') AS region, COALESCE(city, '') AS city, "
    "COALESCE(address, '') AS address, "
    "COALESCE(NULLIF(phone_mobile,''), phone_landline, '') AS phone, "
    "COALESCE(website, '') AS website, COALESCE(email, '') AS email, "
    "COALESCE(rating, 0) AS rating, COALESCE(reviews_count, 0) AS reviews_count, "
    "COALESCE(vk, '') AS vk, COALESCE(instagram, '') AS instagram, "
    "COALESCE(telegram, '') AS telegram, COALESCE(whatsapp, '') AS whatsapp"
)

# Те же имена, но БЕЗ выражений — для внешнего SELECT поверх подзапроса с дедупом.
_SELECT_NAMES = (
    "id, name, category, region, city, address, phone, website, email, "
    "rating, reviews_count, vk, instagram, telegram, whatsapp"
)


EXPORT_COLS = (
    "name, source_category AS category, region, city, address, "
    "TRIM(BOTH ',' FROM CONCAT_WS(',', NULLIF(phone_mobile,''), NULLIF(phone_landline,''))) AS phones, "
    "website, email, schedule, rating, reviews_count, "
    "vk, instagram, telegram, whatsapp, yandex_card_url"
)

# Имена колонок из EXPORT_COLS — для внешнего SELECT.
_EXPORT_NAMES = (
    "name, category, region, city, address, phones, website, email, schedule, "
    "rating, reviews_count, vk, instagram, telegram, whatsapp, yandex_card_url"
)


def _tri(clauses: list[str], state: str, col: str, has_field: Optional[str] = None) -> None:
    """state ∈ {any,yes,no}. yes → col != '' / has_field=TRUE, no → пусто / has_field=FALSE."""
    if state == "yes":
        if has_field:
            clauses.append(f"{has_field} = TRUE")
        else:
            clauses.append(f"COALESCE({col}, '') <> ''")
    elif state == "no":
        if has_field:
            clauses.append(f"{has_field} = FALSE")
        else:
            clauses.append(f"COALESCE({col}, '') = ''")


def _build_base_where(filters: SearchFilters) -> tuple[list[str], list]:
    """Все фильтры КРОМЕ has_*. Используется как build_where, так и build_where_no_contacts."""
    clauses: list[str] = []
    params: list = []

    if filters.categories:
        placeholders = ",".join(["?"] * len(filters.categories))
        clauses.append(f"source_category IN ({placeholders})")
        params.extend(filters.categories)

    if filters.regions:
        placeholders = ",".join(["?"] * len(filters.regions))
        region_clause = f"region IN ({placeholders})"
        region_params = list(filters.regions)
        if filters.cities:
            city_parts = []
            city_params = []
            for entry in filters.cities:
                if "|" in entry:
                    r, c = entry.split("|", 1)
                    city_parts.append("(region = ? AND city ILIKE ?)")
                    city_params.extend([r, c + "%"])
            if city_parts:
                region_clause = f"({region_clause} OR {' OR '.join(city_parts)})"
                region_params.extend(city_params)
        clauses.append(region_clause)
        params.extend(region_params)
    elif filters.cities:
        city_parts = []
        for entry in filters.cities:
            if "|" in entry:
                r, c = entry.split("|", 1)
                city_parts.append("(region = ? AND city ILIKE ?)")
                params.extend([r, c + "%"])
        if city_parts:
            clauses.append(f"({' OR '.join(city_parts)})")
    elif filters.region:
        clauses.append("region = ?")
        params.append(filters.region)
        if filters.city:
            clauses.append("city ILIKE ?")
            params.append(filters.city + "%")

    if filters.keyword:
        kw = f"%{filters.keyword.strip()}%"
        clauses.append("(name ILIKE ? OR address ILIKE ? OR categories ILIKE ?)")
        params.extend([kw, kw, kw])

    if filters.rating_min > 0:
        clauses.append("rating >= ?")
        params.append(filters.rating_min)
    if filters.rating_max is not None and filters.rating_max < 5:
        clauses.append("rating <= ?")
        params.append(filters.rating_max)

    if filters.reviews_min > 0:
        clauses.append("reviews_count >= ?")
        params.append(filters.reviews_min)
    if filters.reviews_max is not None and filters.reviews_max > 0:
        clauses.append("reviews_count <= ?")
        params.append(filters.reviews_max)

    return clauses, params


def build_where(filters: SearchFilters) -> tuple[str, list]:
    """Возвращает (WHERE-условие без слова WHERE, список параметров) — со всеми has_* фильтрами."""
    clauses, params = _build_base_where(filters)

    _tri(clauses, filters.has_phone,    "phone_mobile", has_field="has_phone")
    _tri(clauses, filters.has_website,  "website",      has_field="has_website")
    _tri(clauses, filters.has_email,    "email",        has_field="has_email")
    _tri(clauses, filters.has_vk,        "vk")
    _tri(clauses, filters.has_instagram, "instagram")
    _tri(clauses, filters.has_telegram,  "telegram")

    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


def build_where_no_contacts(filters: SearchFilters) -> tuple[str, list]:
    """Все фильтры БЕЗ has_* (контактных и социальных).

    Используется в `stats_query` чтобы посчитать «полный сегмент» —
    общую популяцию по категориям/региону/городу/keyword/рейтингу/отзывам,
    из которой выгрузка с учётом has_* — лишь подмножество.
    """
    clauses, params = _build_base_where(filters)
    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


# Дедуп: одну и ту же организацию могут «возвращать» несколько строк
# (разные source_category в исходных CSV). Берём по одной — с лучшим рейтингом.
# Ключ дедупа: (LOWER(name), LOWER(address), LOWER(city)).
_DEDUP_PARTITION = (
    "PARTITION BY LOWER(COALESCE(name,'')), LOWER(COALESCE(address,'')), LOWER(COALESCE(city,'')) "
    "ORDER BY (rating IS NULL), rating DESC, reviews_count DESC"
)


def _dedup_inner(where: str, cols: str = "*") -> str:
    """Возвращает подзапрос, отдающий только одну строку на (name, address, city)."""
    return (
        f"(SELECT {cols} FROM ("
        f"  SELECT {cols}, "
        f"  ROW_NUMBER() OVER ({_DEDUP_PARTITION}) AS _rn "
        f"  FROM organizations WHERE {where}"
        f") WHERE _rn = 1)"
    )


def count_query(filters: SearchFilters) -> tuple[str, list]:
    where, params = build_where(filters)
    sql = (
        "SELECT COUNT(*) FROM ("
        "  SELECT 1 FROM ("
        "    SELECT 1, "
        f"    ROW_NUMBER() OVER ({_DEDUP_PARTITION}) AS _rn "
        f"    FROM organizations WHERE {where}"
        "  ) WHERE _rn = 1"
        ")"
    )
    return sql, params


def preview_query(filters: SearchFilters, limit: int = 10) -> tuple[str, list]:
    where, params = build_where(filters)
    sql = (
        f"SELECT {_SELECT_NAMES} FROM ("
        f"  SELECT {SELECT_COLS}, "
        f"  ROW_NUMBER() OVER ({_DEDUP_PARTITION}) AS _rn "
        f"  FROM organizations WHERE {where}"
        ") t WHERE t._rn = 1 "
        "ORDER BY (rating IS NULL), rating DESC, reviews_count DESC "
        "LIMIT ?"
    )
    return sql, params + [limit]


def export_query(filters: SearchFilters, limit: int) -> tuple[str, list]:
    where, params = build_where(filters)
    sql = (
        f"SELECT {_EXPORT_NAMES} FROM ("
        f"  SELECT {EXPORT_COLS}, "
        f"  ROW_NUMBER() OVER ({_DEDUP_PARTITION}) AS _rn "
        f"  FROM organizations WHERE {where}"
        ") t WHERE t._rn = 1 "
        "ORDER BY (rating IS NULL), rating DESC, reviews_count DESC "
        "LIMIT ?"
    )
    return sql, params + [limit]


def top_n_query(filters: SearchFilters, column: str, limit: int = 10) -> tuple[str, list]:
    """Топ-N значений по `column` в текущем сегменте (без has_*).

    column ∈ {region, city, source_category}. Возвращает (name, count) пары.
    Применяет дедуп — одна организация считается как одна единица.
    """
    if column not in ("region", "city", "source_category"):
        raise ValueError(f"Недопустимая колонка для top_n_query: {column}")
    where, params = build_where_no_contacts(filters)
    # Подзапрос: алиасим значение колонки в "value", чтобы не конфликтовать с
    # колонками name/city/address, которые нужны для PARTITION BY дедупа.
    sql = (
        "SELECT value AS name, COUNT(*) AS cnt FROM ("
        f"  SELECT {column} AS value, "
        f"         ROW_NUMBER() OVER ({_DEDUP_PARTITION}) AS _rn "
        f"  FROM organizations WHERE {where}"
        ") t WHERE t._rn = 1 AND value IS NOT NULL AND value <> '' "
        "GROUP BY value ORDER BY cnt DESC LIMIT ?"
    )
    return sql, params + [limit]


def stats_query(filters: SearchFilters) -> tuple[str, list]:
    """Возвращает один SQL: total + 6 счётчиков с COUNT(*) FILTER в одном проходе.

    Применяет дедуп (одна организация = одна единица) и игнорирует has_* фильтры —
    показывает «полный сегмент» по нефильтруемым контактам полям.
    """
    where, params = build_where_no_contacts(filters)
    sql = (
        "SELECT "
        "  COUNT(*) AS total_in_segment, "
        "  COUNT(*) FILTER (WHERE has_phone)                       AS with_phone, "
        "  COUNT(*) FILTER (WHERE has_website)                     AS with_website, "
        "  COUNT(*) FILTER (WHERE has_email)                       AS with_email, "
        "  COUNT(*) FILTER (WHERE COALESCE(vk, '')        <> '')   AS with_vk, "
        "  COUNT(*) FILTER (WHERE COALESCE(instagram, '') <> '')   AS with_instagram, "
        "  COUNT(*) FILTER (WHERE COALESCE(telegram, '')  <> '')   AS with_telegram "
        "FROM ("
        "  SELECT has_phone, has_website, has_email, vk, instagram, telegram, "
        "         name, address, city, rating, reviews_count, "
        f"        ROW_NUMBER() OVER ({_DEDUP_PARTITION}) AS _rn "
        f"  FROM organizations WHERE {where}"
        ") t WHERE t._rn = 1"
    )
    return sql, params


def export_ids_query(filters: SearchFilters, limit: int) -> tuple[str, list]:
    """Возвращает только id организаций — для снимка в Download.org_ids_json."""
    where, params = build_where(filters)
    sql = (
        "SELECT id FROM ("
        "  SELECT id, rating, reviews_count, "
        f"  ROW_NUMBER() OVER ({_DEDUP_PARTITION}) AS _rn "
        f"  FROM organizations WHERE {where}"
        ") t WHERE t._rn = 1 "
        "ORDER BY (rating IS NULL), rating DESC, reviews_count DESC "
        "LIMIT ?"
    )
    return sql, params + [limit]


EXPORT_HEADERS = [
    "Название", "Категория", "Регион", "Город", "Полный адрес",
    "Телефоны", "Сайт", "Email", "График", "Рейтинг", "Отзывов",
    "ВКонтакте", "Instagram", "Telegram", "WhatsApp", "Карточка на Яндекс Картах",
]
