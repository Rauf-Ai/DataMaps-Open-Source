from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Best-effort world geography for parser rows."""

from functools import lru_cache

import requests


_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "DataMaps/1.0 (live-business-search)"}


def _text(value) -> str:
    value = str(value or "").strip()
    return "" if value == "—" else value


def _from_address(address: dict, country_hint: str = "") -> dict[str, str]:
    address = address if isinstance(address, dict) else {}
    return {
        "country": _text(address.get("country")) or _text(country_hint),
        "region": next((_text(address.get(key)) for key in (
            "state", "province", "region", "state_district", "ISO3166-2-lvl4",
        ) if _text(address.get(key))), ""),
        "city": next((_text(address.get(key)) for key in (
            "city", "town", "village", "municipality", "county",
        ) if _text(address.get(key))), ""),
    }


@lru_cache(maxsize=256)
def resolve_location(location: str, country_hint: str = "") -> dict[str, str]:
    """Resolve a requested location without letting network errors stop a task."""
    location = _text(location)
    country_hint = _text(country_hint)
    if not location:
        return {"country": country_hint, "region": "", "city": ""}
    try:
        response = requests.get(
            _NOMINATIM_URL,
            params={"q": ", ".join(filter(None, (location, country_hint))), "format": "jsonv2", "addressdetails": 1, "limit": 1},
            headers=_HEADERS,
            timeout=8,
        )
        response.raise_for_status()
        results = response.json()
        if isinstance(results, list) and results:
            return _from_address(results[0].get("address"), country_hint)
    except (requests.RequestException, ValueError, TypeError):
        pass
    return {"country": country_hint, "region": "", "city": ""}


def apply_geography(row: dict, geography: dict) -> dict:
    """Keep provider geography where present and fill only its absent fields."""
    item = dict(row)
    geography = geography or {}
    for key in ("country", "region", "city"):
        if not _text(item.get(key)):
            item[key] = _text(geography.get(key))
        else:
            item[key] = _text(item.get(key))
    return item
