from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter


def _sheet(wb: Workbook, title: str, headers: list[str]):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    return ws


def _finish(ws) -> None:
    for index, column in enumerate(ws.columns, 1):
        width = max((len(str(cell.value or "")) for cell in column), default=8)
        ws.column_dimensions[get_column_letter(index)].width = min(max(width + 2, 10), 55)
    ws.auto_filter.ref = ws.dimensions


def _service_value(value: object) -> str:
    if isinstance(value, dict):
        name = str(value.get("name") or value.get("title") or "").strip()
        price = str(value.get("price") or "").strip()
        return f"{name} — {price}" if name and price else name or price
    return str(value or "")


def _media_value(media: object) -> str:
    values = media if isinstance(media, list) else []
    return "; ".join(
        f"{item.get('type')}: {item.get('title')}" if isinstance(item, dict) and item.get("title") else str(item.get("type") or "")
        for item in values
        if isinstance(item, dict) and item.get("type")
    ) or None


def write_company_analysis_xlsx(
    path: Path,
    result: dict,
    summary: Optional[dict] = None,
    options: Optional[dict] = None,
) -> None:
    options = options or {
        "photos": True, "reviews": True, "services_prices": True,
        "website": True, "telegram": True, "vk": True,
    }
    wb = Workbook()
    wb.remove(wb.active)
    overview = _sheet(wb, "Итоги", ["Показатель", "Значение"])
    overview.append(["Успешно", result.get("successful_count", 0)])
    overview.append(["Частично", result.get("partial_count", 0)])
    overview.append(["Ошибки", result.get("failed_count", 0)])
    if summary:
        overview.append(["AI-резюме", summary.get("overview") or summary.get("general") or ""])

    companies = _sheet(wb, "Компании", [
        "Название", "Источник", "Адрес", "Телефон", "Сайт", "Рейтинг", "Отзывов",
        "Статус", "Ссылка на карту",
    ])
    reviews = _sheet(wb, "Отзывы", ["Компания", "Автор", "Рейтинг", "Дата", "Текст"]) if options.get("reviews") else None
    services = _sheet(wb, "Услуги и цены", ["Компания", "Тип", "Значение"]) if options.get("services_prices") else None
    channels = _sheet(wb, "Контент каналов", ["Компания", "Канал", "Тип", "URL", "Заголовок", "Текст", "Дата", "Медиа"]) if any(options.get(key) for key in ("website", "telegram", "vk")) else None
    photos = _sheet(wb, "Фотографии", ["Компания", "Файл или URL"]) if options.get("photos") else None
    news = _sheet(wb, "Новости", ["Компания", "Дата", "Заголовок", "Текст"]) if options.get("services_prices") else None
    errors = _sheet(wb, "Ошибки", ["Компания", "Модуль", "Ошибка"])

    for row in result.get("companies") or []:
        company = row.get("company") or {}
        name = company.get("name") or "Без названия"
        companies.append([
            name, row.get("source"), company.get("address"), company.get("phone"),
            company.get("site") or company.get("website"), company.get("rating"),
            company.get("reviews_count"), row.get("status"), row.get("source_url"),
        ])
        if reviews:
            for review in row.get("reviews") or []:
                reviews.append([name, review.get("author"), review.get("rating") or review.get("stars"), review.get("date"), review.get("text")])
        if services:
            for value in row.get("services") or []:
                services.append([name, "Услуга", _service_value(value)])
            for value in row.get("prices") or []:
                services.append([name, "Цена", value if isinstance(value, str) else str(value)])
        if photos:
            for value in row.get("photos") or []:
                photos.append([name, str(value)])
        if news:
            for value in row.get("news") or []:
                item = value if isinstance(value, dict) else {"text": str(value)}
                news.append([
                    name,
                    item.get("date") or item.get("published_at"),
                    item.get("title") or item.get("name"),
                    item.get("text") or item.get("description") or item.get("content"),
                ])
        for key, label in (("web", "Сайт"), ("telegram", "Telegram"), ("vk", "ВКонтакте")):
            channel = row.get(key) or {}
            enabled = options.get("website") if key == "web" else options.get(key)
            if channels and enabled and channel:
                if key == "web":
                    pages = channel.get("pages") or []
                    if pages:
                        for page in pages:
                            channels.append([name, label, "Страница", page.get("url"), page.get("title"), page.get("text") or page.get("description"), None, None])
                    else:
                        channels.append([name, label, "Профиль", channel.get("url"), channel.get("title"), channel.get("description") or channel.get("text"), None, None])
                else:
                    channels.append([name, label, "Профиль", channel.get("url"), channel.get("title"), channel.get("description"), None, None])
                    for post in channel.get("posts") or []:
                        channels.append([name, label, "Пост", channel.get("url"), channel.get("title"), post.get("text"), post.get("date"), _media_value(post.get("media"))])
        for module, error in (row.get("module_errors") or {}).items():
            errors.append([name, module, error])

    for ws in wb.worksheets:
        _finish(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
