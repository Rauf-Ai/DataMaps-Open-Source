from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Экспорт собранных организаций (company dict) в XLSX.

Портирован из YandexMapsParser/yandex_parser.export_excel, адаптирован под
DataMaps: пишет в заданный путь (как app.services.reviews_xlsx), без авто-имени.
Два листа: «Компании» и «Без сайта». Поля общие для Яндекс/2ГИС/объединённого набора.
"""

from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import MultiCellRange
from openpyxl.worksheet.datavalidation import DataValidation

# key → (header, width)
ALL_FIELD_DEFS: "OrderedDict[str, tuple[str, int]]" = OrderedDict([
    ("name",        ("Название",        30)),
    ("phone",       ("Телефон",         22)),
    ("site",        ("Сайт",            28)),
    ("social",      ("Социальные сети", 30)),
    ("address",     ("Адрес",           40)),
    ("country",     ("Страна",          22)),
    ("region",      ("Регион",          24)),
    ("category",    ("Категория",       25)),
    ("rating",      ("Рейтинг",         10)),
    ("reviews",     ("Кол-во отзывов",  16)),
    ("hours",       ("Часы работы",     26)),
    ("services",    ("Товары и услуги", 50)),
    ("features",    ("Особенности",     45)),
    ("price_range", ("Цены",            22)),
    ("has_site",    ("Есть сайт",       12)),
    ("lat",         ("Широта",          14)),
    ("lon",         ("Долгота",         14)),
    ("source",      ("Источник",        12)),
    ("brand_name",  ("Бренд",           24)),
    ("is_franchise", ("Франшиза",       12)),
    ("branch_count", ("Количество точек", 16)),
    ("matched_queries", ("Найдено по запросам", 40)),
    ("map_url",     ("Ссылка на карты", 36)),
    ("parsed_at",   ("Актуальность",    22)),
])

DEFAULT_FIELDS = list(ALL_FIELD_DEFS.keys())

HEADER_FILL = PatternFill("solid", fgColor="D9EAD3")
HEADER_FONT = Font(bold=True)


def _build_columns(selected: list[str]) -> list[tuple]:
    cols = []
    for key in selected:
        if key in ALL_FIELD_DEFS:
            header, width = ALL_FIELD_DEFS[key]
            cols.append((key, header, width))
    return cols


def write_companies_xlsx(file_path, companies: list[dict],
                         selected_fields: Optional[list[str]] = None) -> None:
    """Записать companies в XLSX по пути file_path."""
    cols = _build_columns(selected_fields or DEFAULT_FIELDS)
    wb = Workbook()

    def _write_sheet(ws, rows):
        for ci, (_, header, _) in enumerate(cols, 1):
            cell = ws.cell(1, ci, header)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for ri, c in enumerate(rows, 2):
            for ci, (key, _, _) in enumerate(cols, 1):
                val = c.get(key)
                if key == "matched_queries" and isinstance(val, list):
                    val = "; ".join(str(item) for item in val)
                if key == "is_franchise":
                    val = "Да" if val else "Нет"
                if key in ("lat", "lon") and not isinstance(val, float):
                    val = None
                if key == "rating" and not isinstance(val, float):
                    val = None
                cell = ws.cell(ri, ci, val)
                if key in ("lat", "lon") and val is not None:
                    cell.number_format = "0.000000"
                elif key == "rating" and val is not None:
                    cell.number_format = "0.0"
                elif key == "reviews":
                    cell.number_format = "0"
                elif key == "parsed_at" and val is not None:
                    cell.number_format = "dd.mm.yyyy hh:mm:ss"
                if key == "site" and isinstance(val, str) and val.startswith(("https://", "http://")):
                    cell.hyperlink = val
                    cell.style = "Hyperlink"
        for ci, (_, _, width) in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(ci)].width = width
        n = len(rows)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{n + 1}"
        if n > 0:
            for ci, (key, _, _) in enumerate(cols, 1):
                if key == "has_site":
                    dv = DataValidation(
                        type="list", formula1='"Да,Нет"', allow_blank=False)
                    dv.sqref = MultiCellRange(
                        f"{get_column_letter(ci)}2:{get_column_letter(ci)}{n + 1}")
                    ws.add_data_validation(dv)
                    break

    ws1 = wb.active
    ws1.title = "Компании"
    _write_sheet(ws1, companies)

    ws2 = wb.create_sheet("Без сайта")
    _write_sheet(ws2, [c for c in companies if c.get("has_site") == "Нет"])

    wb.save(str(file_path))
