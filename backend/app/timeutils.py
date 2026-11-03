from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC datetime — для согласованности с SQLite-хранилищем без tz."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
