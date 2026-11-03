from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import logging
import os
import uuid
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import (
    FastAPI,
    Request
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import Package, Transaction
from app.ratelimit import limiter
from app.routers import account, admin, admin_control, ai, auth, chat, export, live_lab, parser, payments, reviews, search, social, support, telegram
from app.services.export_service import schedule_cleanup
from app.services.subscription_billing import renew_due_subscriptions


settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")


def _migrate_downloads_add_org_ids() -> None:
    """Idempotent: добавляет колонку downloads.org_ids_json если её нет (SQLite)."""
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(downloads)"))}
        if "org_ids_json" not in cols:
            conn.execute(text("ALTER TABLE downloads ADD COLUMN org_ids_json TEXT"))
            conn.commit()
            log.info("downloads.org_ids_json добавлен")


def _migrate_review_tasks_add_deep() -> None:
    """Idempotent: добавляет колонку review_tasks.deep если её нет (SQLite)."""
    from sqlalchemy import text
    with engine.connect() as conn:
        # таблица создаётся через create_all, проверяем существование
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "review_tasks" not in tables:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(review_tasks)"))}
        if "deep" not in cols:
            conn.execute(text("ALTER TABLE review_tasks ADD COLUMN deep INTEGER DEFAULT 0 NOT NULL"))
            conn.commit()
            log.info("review_tasks.deep добавлен")


def _migrate_support_messages_add_image() -> None:
    """Idempotent: добавляет колонку support_messages.image_path если её нет (SQLite)."""
    from sqlalchemy import text
    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "support_messages" not in tables:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(support_messages)"))}
        if "image_path" not in cols:
            conn.execute(text("ALTER TABLE support_messages ADD COLUMN image_path TEXT"))
            conn.commit()
            log.info("support_messages.image_path добавлен")


def _migrate_businesses_add_chain_fields() -> None:
    """Idempotent SQLite migration for franchise grouping metadata."""
    from sqlalchemy import text
    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "businesses" not in tables:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(businesses)"))}
        statements = {
            "brand_name": "ALTER TABLE businesses ADD COLUMN brand_name VARCHAR(500)",
            "chain_key": "ALTER TABLE businesses ADD COLUMN chain_key VARCHAR(500)",
            "is_franchise": "ALTER TABLE businesses ADD COLUMN is_franchise INTEGER DEFAULT 0 NOT NULL",
            "branch_count": "ALTER TABLE businesses ADD COLUMN branch_count INTEGER DEFAULT 1 NOT NULL",
        }
        changed = False
        for column, statement in statements.items():
            if column not in cols:
                conn.execute(text(statement))
                changed = True
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_businesses_chain_key ON businesses (chain_key)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_businesses_brand_name ON businesses (brand_name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_businesses_is_franchise ON businesses (is_franchise)"))
        if changed:
            log.info("businesses: добавлены поля сети/франшизы")
        conn.commit()


def _migrate_businesses_add_country() -> None:
    """Idempotent SQLite migration for internationally resolved country."""
    from sqlalchemy import text

    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "businesses" not in tables:
            return
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(businesses)"))}
        if "country" not in columns:
            conn.execute(text("ALTER TABLE businesses ADD COLUMN country VARCHAR(120)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_businesses_country ON businesses (country)"))
        conn.commit()


def _migrate_parser_tasks_add_progress_json() -> None:
    """Idempotent SQLite migration for live parser progress snapshots."""
    from sqlalchemy import text
    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "parser_tasks" not in tables:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(parser_tasks)"))}
        if "progress_json" not in cols:
            conn.execute(text("ALTER TABLE parser_tasks ADD COLUMN progress_json TEXT"))
            conn.commit()
            log.info("parser_tasks.progress_json добавлен")


def _migrate_task_control_fields() -> None:
    """Idempotent SQLite migration for unified task controls."""
    from sqlalchemy import text

    if engine.dialect.name != "sqlite":
        return
    table_names = (
        "export_tasks",
        "parser_tasks",
        "review_tasks",
        "company_analysis_tasks",
        "social_analysis_tasks",
    )
    statements = {
        "cancel_requested": "BOOLEAN DEFAULT 0 NOT NULL",
        "priority": "INTEGER DEFAULT 0 NOT NULL",
        "retry_of_id": "VARCHAR(36)",
        "started_at": "DATETIME",
        "finished_at": "DATETIME",
    }
    with engine.connect() as conn:
        tables = {
            row[0] for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        for table_name in table_names:
            if table_name not in tables:
                continue
            columns = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))
            }
            for column_name, column_sql in statements.items():
                if column_name not in columns:
                    conn.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"
                    ))
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_{table_name}_retry_of_id "
                f"ON {table_name} (retry_of_id)"
            ))
        conn.commit()


def init_db() -> None:
    os.makedirs(os.path.dirname(settings.database_url.replace("sqlite:///", "")) or ".", exist_ok=True)
    Base.metadata.create_all(bind=engine)
    from app.services import support_kb_service
    try:
        with engine.begin() as conn:
            support_kb_service.ensure_schema(conn)
            result = support_kb_service.sync_knowledge_base(
                conn, support_kb_service.default_root(),
            )
        log.info("support knowledge base synchronized: %d documents, %d chunks", result.documents, result.chunks)
    except support_kb_service.KnowledgeBaseUnavailable as exc:
        log.error("support knowledge base unavailable: %s", exc)
    _migrate_downloads_add_org_ids()
    _migrate_review_tasks_add_deep()
    _migrate_support_messages_add_image()
    _migrate_businesses_add_chain_fields()
    _migrate_businesses_add_country()
    _migrate_parser_tasks_add_progress_json()
    _migrate_task_control_fields()
    db = SessionLocal()
    try:
        referenced = set(db.execute(select(Transaction.package_id).distinct()).scalars().all())
        legacy = db.execute(select(Package)).scalars().all()
        for package in legacy:
            if package.id in referenced:
                package.is_active = False
            else:
                db.delete(package)
        if legacy:
            db.commit()
            log.info("Старые разовые пакеты выведены из продажи: %d", len(legacy))
    finally:
        db.close()


scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(schedule_cleanup, "interval", minutes=15, id="cleanup_exports", replace_existing=True)
    if not settings.local_mode:
        scheduler.add_job(
            renew_due_subscriptions,
            "interval",
            hours=1,
            id="renew_subscriptions",
            replace_existing=True,
        )
    scheduler.start()
    log.info("Scheduler запущен")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="DataMaps — справочник организаций России", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/api/health")
async def health():
    return {"ok": True}


app.include_router(auth.router)
app.include_router(search.router)
app.include_router(export.router)
app.include_router(payments.router)
app.include_router(account.router)
app.include_router(admin.router)
app.include_router(admin_control.router)
app.include_router(reviews.router)
app.include_router(ai.router)
app.include_router(chat.router)
app.include_router(parser.router)
app.include_router(live_lab.router)
app.include_router(support.router)
app.include_router(telegram.router)
app.include_router(social.router)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:12]
    log.exception("unhandled error code=GEN-500 request_id=%s on %s %s",
                  request_id, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "GEN-500",
                "message": "Внутренняя ошибка",
                "hint": "Посмотрите backend logs по request_id.",
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id},
    )
