from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import math
import re
from collections import Counter
from difflib import SequenceMatcher
from urllib.parse import urlparse


_EMPTY = {None, "", "—", "-"}


def _text(value: Any) -> str:
    return "" if value in _EMPTY else str(value).strip()


def _norm(value: Any) -> str:
    text = _text(value).lower().replace("ё", "е")
    text = re.sub(r"\b(?:ооо|ао|пао|ип|филиал|магазин)\b", " ", text)
    return re.sub(r"[^a-zа-я0-9]+", " ", text).strip()


def _phone(value: Any) -> str:
    digits = re.sub(r"\D+", "", _text(value).split(",")[0])
    return digits[-10:] if len(digits) >= 10 else digits


def _domain(row: dict) -> str:
    value = _text(row.get("site") or row.get("website"))
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return (parsed.hostname or "").lower().removeprefix("www.")


def _coords_close(a: dict, b: dict, tolerance: float = 0.0008) -> bool:
    try:
        return math.hypot(float(a.get("lat")) - float(b.get("lat")), float(a.get("lon")) - float(b.get("lon"))) <= tolerance
    except (TypeError, ValueError):
        return False


def _same_physical(a: dict, b: dict) -> bool:
    source_a, source_b = _norm(a.get("source")), _norm(b.get("source"))
    oid_a, oid_b = _text(a.get("oid") or a.get("source_id")), _text(b.get("oid") or b.get("source_id"))
    if source_a and source_a == source_b and oid_a and oid_b:
        return oid_a == oid_b
    name_a, name_b = _norm(a.get("name")), _norm(b.get("name"))
    address_a, address_b = _norm(a.get("address")), _norm(b.get("address"))
    same_address = bool(address_a and address_a == address_b)
    if name_a and name_a == name_b and same_address:
        return True
    if name_a and name_a == name_b and _coords_close(a, b):
        return True
    phone_a, phone_b = _phone(a.get("phone")), _phone(b.get("phone"))
    if phone_a and phone_a == phone_b and (same_address or not address_a or not address_b):
        return True
    domain_a, domain_b = _domain(a), _domain(b)
    tokens_a, tokens_b = set(name_a.split()), set(name_b.split())
    token_similarity = bool(tokens_a and tokens_b and len(tokens_a & tokens_b) / len(tokens_a | tokens_b) >= 0.75)
    similar_name = bool(
        name_a and name_b
        and (SequenceMatcher(None, name_a, name_b).ratio() >= 0.82 or token_similarity)
    )
    return bool(domain_a and domain_a == domain_b and similar_name and (same_address or _coords_close(a, b)))


def _queries(row: dict) -> list[str]:
    values = list(row.get("matched_queries") or [])
    if _text(row.get("matched_query")):
        values.append(_text(row.get("matched_query")))
    return list(dict.fromkeys(value for value in values if value))


def _merge_rows(current: dict, candidate: dict) -> dict:
    merged = dict(current)
    for key, value in candidate.items():
        if key in {"matched_query", "matched_queries"}:
            continue
        if _text(value) and not _text(merged.get(key)):
            merged[key] = value
        elif key == "reviews":
            try:
                merged[key] = max(int(current.get(key) or 0), int(candidate.get(key) or 0))
            except (TypeError, ValueError):
                pass
    merged["matched_queries"] = list(dict.fromkeys(_queries(current) + _queries(candidate)))
    return merged


def deduplicate_cards(rows: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for raw in rows:
        row = dict(raw)
        row["matched_queries"] = _queries(row)
        match = next((index for index, existing in enumerate(merged) if _same_physical(existing, row)), None)
        if match is None:
            merged.append(row)
        else:
            merged[match] = _merge_rows(merged[match], row)
    return merged


def annotate_franchise_groups(rows: list[dict]) -> list[dict]:
    names = [_norm(row.get("chain_name") or row.get("brand_name") or row.get("name")) for row in rows]
    counts = Counter(name for name in names if name)
    keys: list[str] = []
    for row, normalized_name in zip(rows, names):
        explicit_key = _text(row.get("chain_id") or row.get("chain_key"))
        is_chain = bool(explicit_key or _text(row.get("chain_name") or row.get("brand_name")) or counts.get(normalized_name, 0) >= 2)
        keys.append(f"source:{explicit_key}" if explicit_key else (f"name:{normalized_name}" if is_chain else ""))
    key_counts = Counter(key for key in keys if key)
    out: list[dict] = []
    for row, normalized_name, key in zip(rows, names, keys):
        item = dict(row)
        explicit_key = _text(row.get("chain_id") or row.get("chain_key"))
        explicit_name = _text(row.get("chain_name") or row.get("brand_name"))
        is_chain = bool(explicit_key or explicit_name or counts.get(normalized_name, 0) >= 2)
        branch_count = key_counts.get(key, 1) if is_chain else 1
        item.update({
            "brand_name": explicit_name or _text(row.get("name")),
            "chain_key": key or None,
            "is_franchise": is_chain,
            "branch_count": max(1, branch_count),
        })
        out.append(item)
    return out


def filter_franchises(rows: list[dict], mode: str) -> list[dict]:
    mode = mode if mode in {"any", "yes", "no"} else "any"
    if mode == "any":
        return rows
    want = mode == "yes"
    return [row for row in rows if bool(row.get("is_franchise")) is want]
