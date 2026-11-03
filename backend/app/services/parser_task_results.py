from __future__ import annotations

"""Storage for cards that are visible while a map parser task is still running."""

import json
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ParserTask, ParserTaskResult


_MAX_APPEND_RETRIES = 5


def task_result_identity(row: dict[str, Any]) -> str:
    """Return a stable identity for a provider card without exposing it to clients."""
    raw_source = str(row.get("source") or "").strip().casefold()
    source = {
        "yandex": "yandex",
        "яндекс": "yandex",
        "яндекс карты": "yandex",
        "google": "google",
        "google maps": "google",
        "2gis": "2gis",
        "2гис": "2gis",
    }.get(raw_source, raw_source)
    external_id = str(row.get("oid") or row.get("source_id") or row.get("id") or "").strip()
    if external_id:
        return f"{source or 'maps'}:id:{external_id.casefold()}"
    map_url = str(row.get("map_url") or row.get("source_url") or "").strip()
    if map_url:
        return f"{source or 'maps'}:url:{map_url.casefold()}"
    name = str(row.get("name") or "").strip().casefold()
    address = str(row.get("address") or "").strip().casefold()
    city = str(row.get("city") or row.get("region") or "").strip().casefold()
    return f"{source or 'maps'}:card:{name}|{address}|{city}"


def _json_safe_card(row: dict[str, Any]) -> dict[str, Any]:
    """Make a detached JSON-safe copy so worker-owned objects cannot mutate later."""
    value = json.loads(json.dumps(row, ensure_ascii=False, default=str))
    if not isinstance(value, dict):
        return {}
    value.pop("position", None)
    return value


def append_task_results(db: Session, task_id: str, rows: list[dict]) -> int:
    """Append unseen card snapshots, returning the number of rows actually stored.

    The caller owns the transaction: this function flushes but deliberately does
    not commit so task progress and newly visible cards are published together.
    """
    if not rows:
        return 0
    # PostgreSQL serializes concurrent pumps for one task here. SQLite ignores
    # FOR UPDATE, so the constraints plus the retry loop below remain required.
    db.execute(
        select(ParserTask.id)
        .where(ParserTask.id == task_id)
        .with_for_update()
    ).scalar_one_or_none()
    existing = set(db.scalars(
        select(ParserTaskResult.identity_key).where(ParserTaskResult.task_id == task_id)
    ).all())
    last_position = db.scalar(
        select(func.max(ParserTaskResult.position)).where(ParserTaskResult.task_id == task_id)
    ) or 0
    appended = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        data = _json_safe_card(raw)
        identity_key = task_result_identity(data)
        if identity_key in existing:
            continue
        for attempt in range(_MAX_APPEND_RETRIES):
            candidate_position = last_position + 1
            try:
                # A savepoint keeps task progress and prior card inserts alive
                # when another worker wins the same position race.
                with db.begin_nested():
                    db.add(ParserTaskResult(
                        task_id=task_id,
                        position=candidate_position,
                        identity_key=identity_key,
                        data_json=json.dumps(data, ensure_ascii=False),
                    ))
                    db.flush()
            except IntegrityError:
                already_written = db.scalar(
                    select(ParserTaskResult.id).where(
                        ParserTaskResult.task_id == task_id,
                        ParserTaskResult.identity_key == identity_key,
                    )
                )
                if already_written is not None:
                    existing.add(identity_key)
                    break
                last_position = db.scalar(
                    select(func.max(ParserTaskResult.position)).where(
                        ParserTaskResult.task_id == task_id
                    )
                ) or 0
                if attempt == _MAX_APPEND_RETRIES - 1:
                    raise
                continue
            existing.add(identity_key)
            last_position = candidate_position
            appended += 1
            break
    return appended


def replace_task_results(db: Session, task_id: str, rows: list[dict]) -> None:
    """Replace streamed snapshots with the definitive, filtered task result."""
    db.execute(delete(ParserTaskResult).where(ParserTaskResult.task_id == task_id))
    db.flush()
    append_task_results(db, task_id, rows)


def read_task_results(
    db: Session, task_id: str, after: int, limit: int = 200,
) -> tuple[list[dict], int]:
    """Read the next ordered page of cards and the cursor to use afterwards."""
    if after < 0:
        raise ValueError("after must be non-negative")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    records = db.scalars(
        select(ParserTaskResult)
        .where(
            ParserTaskResult.task_id == task_id,
            ParserTaskResult.position > after,
        )
        .order_by(ParserTaskResult.position, ParserTaskResult.id)
        .limit(limit)
    ).all()
    items: list[dict] = []
    next_after = after
    for record in records:
        try:
            data = json.loads(record.data_json)
        except (TypeError, json.JSONDecodeError):
            data = {}
        item = data if isinstance(data, dict) else {}
        item["position"] = record.position
        items.append(item)
        next_after = record.position
    return items, next_after
