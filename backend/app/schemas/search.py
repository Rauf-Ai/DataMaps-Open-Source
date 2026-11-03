from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from pydantic import BaseModel, Field


TriState = Literal["any", "yes", "no"]


class SearchFilters(BaseModel):
    categories: list[str] = Field(default_factory=list)
    region: Optional[str] = None
    city: Optional[str] = None
    regions: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    keyword: Optional[str] = None

    has_phone: TriState = "any"
    has_website: TriState = "any"
    has_email: TriState = "any"
    has_vk: TriState = "any"
    has_instagram: TriState = "any"
    has_telegram: TriState = "any"

    rating_min: float = 0.0
    rating_max: float = 5.0
    reviews_min: int = 0
    reviews_max: Optional[int] = None


class OrgPreview(BaseModel):
    id: int
    name: str
    category: str = ""
    region: str = ""
    city: str
    address: str
    phone: str
    website: str
    email: str = ""
    rating: float
    reviews_count: int = 0
    vk: str = ""
    instagram: str = ""
    telegram: str = ""
    whatsapp: str = ""


class TopItem(BaseModel):
    name: str
    count: int


class SearchStats(BaseModel):
    total_in_segment: int = 0
    with_phone: int = 0
    with_website: int = 0
    with_email: int = 0
    with_vk: int = 0
    with_instagram: int = 0
    with_telegram: int = 0
    top_regions: list[TopItem] = Field(default_factory=list)
    top_cities: list[TopItem] = Field(default_factory=list)
    top_categories: list[TopItem] = Field(default_factory=list)


class SearchResponse(BaseModel):
    total: int
    preview: list[OrgPreview]
    stats: Optional[SearchStats] = None
