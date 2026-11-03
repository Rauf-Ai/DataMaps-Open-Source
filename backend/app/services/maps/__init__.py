from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Пакет парсеров карт (Яндекс / 2ГИС): живой поиск, обогащение карточки, сбор лендинг-ZIP.

Портирован из пользовательского проекта YandexMapsParser и адаптирован под DataMaps:
константы вынесены в app.config.Settings, блокирующие вызовы оборачиваются в
asyncio.to_thread на уровне оркестратора (app.services.maps_service).
"""
