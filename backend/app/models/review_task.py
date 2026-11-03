from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from datetime import datetime
from sqlalchemy import Boolean, String, Integer, DateTime, ForeignKey, Enum, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.export_task import ExportStatus


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[ExportStatus] = mapped_column(
        Enum(ExportStatus), default=ExportStatus.queued, nullable=False, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    org_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    star_min: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    star_max: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    deep: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    credits_charged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    download_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    download_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_of_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
