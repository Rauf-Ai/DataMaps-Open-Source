from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Генерация XLSX с анализом отзывов: лист «Сводка» + лист на каждую организацию."""

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


SUMMARY_HEADERS = [
    "Название", "Адрес", "Рейтинг", "Отзывов", "Статус", "Общее впечатление",
    "Особенности", "Плюсы", "Минусы", "Сервис", "Скорость", "Цены", "Качество",
]
REVIEW_HEADERS = ["Дата", "Автор", "Звёзды", "Текст", "Ответ компании"]


HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
WRAP = Alignment(wrap_text=True, vertical="top")


def _safe_sheet_name(name: str, used: set[str]) -> str:
    """Имена листов в Excel: ≤31 символ, без / \\ ? * [ ] :."""
    cleaned = "".join("_" if c in r"/\\?*[]:" else c for c in name)[:28]
    base = cleaned or "org"
    candidate = base
    i = 2
    while candidate in used:
        suffix = f"_{i}"
        candidate = (base[: 28 - len(suffix)]) + suffix
        i += 1
    used.add(candidate)
    return candidate


def _style_header(ws, headers: list[str]) -> None:
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL


def _autosize(ws, max_lens: list[int], cap: int = 80) -> None:
    for i, ln in enumerate(max_lens, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(ln + 2, 10), cap)


def _fmt_pros_cons(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"• {x}" for x in value)
    return str(value or "")


def _fmt_date(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return ""


def write_reviews_xlsx(file_path: Path, results: list[dict]) -> None:
    """results = [{org_id, name, address, rating, reviews_count, summary, reviews:[...]}, ...]

    summary — словарь с ключами general/features/pros/cons/service/speed/prices/quality.
    reviews — список dict из парсера: name, date (timestamp), text, stars, answer.
    """
    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "Сводка"
    _style_header(summary_ws, SUMMARY_HEADERS)
    summary_ws.freeze_panes = "A2"

    summary_lens = [len(h) for h in SUMMARY_HEADERS]
    used_names: set[str] = {"Сводка"}

    for org in results:
        summary = org.get("summary") or {}
        err = org.get("error")
        summary_err = org.get("summary_error")
        actual_reviews = len(org.get("reviews") or [])
        if err:
            status = f"Ошибка парсинга: {err}"
        elif actual_reviews == 0:
            status = "Нет отзывов под фильтр"
        elif summary_err:
            status = f"OK · {actual_reviews} отзывов · AI недоступен: {summary_err[:80]}"
        else:
            status = f"OK · {actual_reviews} отзывов · AI сводка готова"
        row = [
            org.get("name") or "",
            org.get("address") or "",
            org.get("rating") or "",
            actual_reviews,
            status,
            summary.get("general") or "",
            summary.get("features") or "",
            _fmt_pros_cons(summary.get("pros")),
            _fmt_pros_cons(summary.get("cons")),
            summary.get("service") or "",
            summary.get("speed") or "",
            summary.get("prices") or "",
            summary.get("quality") or "",
        ]
        summary_ws.append(row)
        last_row = summary_ws.max_row
        for c in range(1, len(row) + 1):
            summary_ws.cell(row=last_row, column=c).alignment = WRAP
        for i, v in enumerate(row):
            ln = max((len(s) for s in str(v).split("\n")), default=0)
            if ln > summary_lens[i]:
                summary_lens[i] = ln

        sheet_name = _safe_sheet_name(org.get("name") or f"org_{org.get('org_id')}", used_names)
        ws = wb.create_sheet(title=sheet_name)
        _style_header(ws, REVIEW_HEADERS)
        ws.freeze_panes = "A2"
        rev_lens = [len(h) for h in REVIEW_HEADERS]
        for r in org.get("reviews") or []:
            row = [
                _fmt_date(r.get("date")),
                r.get("name") or "",
                r.get("stars") or "",
                r.get("text") or "",
                r.get("answer") or "",
            ]
            ws.append(row)
            ws.cell(row=ws.max_row, column=4).alignment = WRAP
            ws.cell(row=ws.max_row, column=5).alignment = WRAP
            for i, v in enumerate(row):
                ln = min(len(str(v)), 80)
                if ln > rev_lens[i]:
                    rev_lens[i] = ln
        _autosize(ws, rev_lens)

    _autosize(summary_ws, summary_lens)
    wb.save(file_path)
