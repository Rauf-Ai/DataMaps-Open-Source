from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from app.config import get_settings
from app.db import SessionLocal, get_duck
from app.models import Download, ExportStatus, ExportTask, User
from app.schemas.search import SearchFilters
from app.security import generate_token
from app.services.admin_incident_service import record_incident, safe_text
from app.services.admin_task_service import finalize_task_unless_cancelled, stop_if_cancelled
from app.services.search_service import EXPORT_HEADERS, count_query, export_query, export_ids_query
from app.timeutils import utcnow


settings = get_settings()


def _safe_filename(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE).strip("_")
    return s[:60] or "all"


def build_filename(filters: SearchFilters) -> str:
    parts = [
        _safe_filename(filters.categories[0]) if filters.categories else "all",
        _safe_filename(filters.city or filters.region or "russia"),
        datetime.now().strftime("%Y-%m-%d"),
    ]
    return "spravochnik_" + "_".join(parts) + ".xlsx"


def _record_export_failure(db, task_id: str, exc: Exception) -> None:
    db.rollback()
    task = db.get(ExportTask, task_id)
    if not task:
        return
    task.status = ExportStatus.failed
    task.finished_at = utcnow()
    task.error = f"[EXPORT-500] {type(exc).__name__}: {safe_text(str(exc))[:400]}"
    record_incident(
        db,
        code="EXPORT-500",
        source="export_service",
        message=f"{type(exc).__name__} while running export task",
        severity="critical",
        user_id=task.user_id,
        task_type="export",
        task_id=task.id,
        details={"exception_type": type(exc).__name__},
    )


def run_export(task_id: str) -> None:
    """Запускается в фоне через BackgroundTasks. Своя сессия SQLAlchemy."""
    db = SessionLocal()
    try:
        task: Optional[ExportTask] = db.get(ExportTask, task_id)
        if not task:
            return
        if stop_if_cancelled(db, task):
            return
        user: Optional[User] = db.get(User, task.user_id)
        if not user:
            task.status = ExportStatus.failed
            task.error = "Пользователь не найден"
            db.commit()
            return
        task.status = ExportStatus.running
        task.started_at = task.started_at or utcnow()
        db.commit()
        try:
            if stop_if_cancelled(db, task):
                return
            filters = SearchFilters.model_validate_json(task.filters_json)
            limit = task.requested_rows
            with get_duck() as cur:
                if stop_if_cancelled(db, task):
                    return
                total = cur.execute(*count_query(filters)).fetchone()[0]
                effective = min(limit, total, settings.export_max_rows)
                if not settings.local_mode:
                    effective = min(effective, user.credits_balance)
                if effective <= 0:
                    task.status = ExportStatus.failed
                    task.error = "Нет данных для выгрузки или нет кредитов"
                    db.commit()
                    return
                ids_sql, ids_params = export_ids_query(filters, effective)
                org_ids = [int(r[0]) for r in cur.execute(ids_sql, ids_params).fetchall() if r[0] is not None]
                sql, params = export_query(filters, effective)
                cur.execute(sql, params)
                # Стримим в xlsx
                file_path = _write_xlsx(cur, task_id, filters, effective, task=task, db=db)
                if file_path is None or stop_if_cancelled(db, task):
                    return

            # Списываем кредиты и фиксируем download
            if stop_if_cancelled(db, task):
                return
            if not finalize_task_unless_cancelled(db, task, ExportStatus.done):
                try:
                    file_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return
            if not settings.local_mode:
                user.credits_balance -= effective
                user.credits_spent += effective
            token = generate_token()
            dl = Download(
                user_id=user.id,
                export_task_id=task.id,
                filters_json=task.filters_json,
                row_count=effective,
                credits_charged=0 if settings.local_mode else effective,
                file_path=str(file_path),
                expires_at=utcnow() + timedelta(hours=settings.export_file_ttl_hours),
                org_ids_json=json.dumps(org_ids),
            )
            db.add(dl)
            db.flush()
            task.download_id = dl.id
            task.download_token = token
            task.progress = 100
            task.finished_at = utcnow()
            db.commit()
        except Exception as exc:  # noqa: BLE001
            _record_export_failure(db, task_id, exc)
    except Exception as exc:  # noqa: BLE001
        _record_export_failure(db, task_id, exc)
    finally:
        current = db.get(ExportTask, task_id)
        if current and current.status in {
            ExportStatus.done, ExportStatus.failed, ExportStatus.partial, ExportStatus.canceled
        } and current.finished_at is None:
            current.finished_at = utcnow()
            db.commit()
        db.close()


def _write_xlsx(cur, task_id: str, filters: SearchFilters, effective: int, *, task: ExportTask, db) -> Optional[Path]:
    os.makedirs(settings.export_dir, exist_ok=True)
    file_path = Path(settings.export_dir) / f"{task_id}.xlsx"

    wb = Workbook(write_only=False)
    ws = wb.active
    ws.title = "Организации"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
    ws.append(EXPORT_HEADERS)
    for c, _ in enumerate(EXPORT_HEADERS, start=1):
        cell = ws.cell(row=1, column=c)
        cell.font = header_font
        cell.fill = header_fill
    ws.freeze_panes = "A2"

    max_lens = [len(h) for h in EXPORT_HEADERS]
    written = 0
    step = max(1, effective // 100)

    while True:
        if stop_if_cancelled(db, task):
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError:
                pass
            return None
        rows = cur.fetchmany(2000)
        if not rows:
            break
        for r in rows:
            if stop_if_cancelled(db, task):
                try:
                    if file_path.exists():
                        file_path.unlink()
                except OSError:
                    pass
                return None
            if written >= effective:
                break
            row_values = list(r)
            # rating → строка с одним знаком, если есть
            if row_values[9] is not None:
                row_values[9] = round(float(row_values[9]), 1)
            ws.append(row_values)
            written += 1
            for i, v in enumerate(row_values):
                if v is None:
                    continue
                ln = len(str(v))
                if ln > max_lens[i]:
                    max_lens[i] = ln
            if written % step == 0:
                task.progress = min(99, int(written * 100 / effective))
                db.commit()
        if written >= effective:
            break

    for i, ln in enumerate(max_lens, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(ln + 2, 10), 60)

    wb.save(file_path)
    return file_path


def schedule_cleanup() -> int:
    """Удаляет файлы выгрузок с истёкшим TTL. Возвращает количество удалённых."""
    db = SessionLocal()
    removed = 0
    try:
        now = utcnow()
        items = db.query(Download).filter(Download.expires_at < now, Download.file_path.isnot(None)).all()
        for d in items:
            try:
                if d.file_path and os.path.exists(d.file_path):
                    os.remove(d.file_path)
            except OSError:
                pass
            d.file_path = None
            removed += 1
        if removed:
            db.commit()
    finally:
        db.close()
    return removed


def new_task_id() -> str:
    return str(uuid.uuid4())
