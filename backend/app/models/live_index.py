from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Business(Base):
    """Нормализованная карточка организации в постоянном Live Index."""

    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    region: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    city: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
    phone_norm: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    website_norm: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    social: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    reviews_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    hours: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    services: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    features: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_range: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brand_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    chain_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    is_franchise: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    branch_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False, index=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, index=True)
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, index=True)
    freshness_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False, index=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_business_identity_name_address", "name", "address"),
    )


class BusinessSource(Base):
    """Идентификатор одной и той же организации во внешнем источнике."""

    __tablename__ = "business_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_parsed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_business_source_identity"),
    )


class BusinessSnapshot(Base):
    """История изменений карточки организации."""

    __tablename__ = "business_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    changed_fields_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class SavedResult(Base):
    """Постоянный пользовательский набор организаций, полученный парсингом."""

    __tablename__ = "saved_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    parser_task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class SavedResultItem(Base):
    """Связь сохранённой выгрузки с Live Index business_id."""

    __tablename__ = "saved_result_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    saved_result_id: Mapped[int] = mapped_column(ForeignKey("saved_results.id"), nullable=False, index=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("saved_result_id", "business_id", name="uq_saved_result_business"),
    )
