from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import os
import threading
from contextlib import contextmanager

import duckdb
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from app.config import get_settings


settings = get_settings()


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_duck_lock = threading.Lock()
_duck_conn: duckdb.Optional[DuckDBPyConnection] = None


def _get_duck_singleton() -> duckdb.DuckDBPyConnection:
    """Read-only соединение, чтобы не конфликтовать с импортом.

    Если файла ещё нет — создаём пустой через короткое writable-подключение,
    затем переоткрываем в read_only. При любых I/O-ошибках возвращаем None,
    а вызывающий код (через _safe_fetch и т.п.) корректно обработает это.
    """
    global _duck_conn
    with _duck_lock:
        if _duck_conn is not None:
            return _duck_conn
        os.makedirs(os.path.dirname(settings.duckdb_path), exist_ok=True)
        if not os.path.exists(settings.duckdb_path):
            tmp = duckdb.connect(settings.duckdb_path)
            tmp.close()
        _duck_conn = duckdb.connect(settings.duckdb_path, read_only=True)
        _duck_conn.execute("PRAGMA threads=4;")
        return _duck_conn


def reset_duck_singleton() -> None:
    """Закрывает текущее соединение — для подхвата свежей БД после импорта."""
    global _duck_conn
    with _duck_lock:
        if _duck_conn is not None:
            try:
                _duck_conn.close()
            except Exception:
                pass
        _duck_conn = None


@contextmanager
def get_duck() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a per-call cursor on the singleton DuckDB connection.

    DuckDB allows multiple cursors on one connection; we serialize via cursor().
    """
    conn = _get_duck_singleton()
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()
