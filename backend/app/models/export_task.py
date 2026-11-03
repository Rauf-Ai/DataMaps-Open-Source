from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import enum
from datetime import datetime
from sqlalchemy import Boolean, String, Integer, DateTime, ForeignKey, Enum, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExportStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    partial = "partial"
    canceled = "canceled"


class ExportTask(Base):
    __tablename__ = "export_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[ExportStatus] = mapped_column(
        Enum(ExportStatus), default=ExportStatus.queued, nullable=False, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    download_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    download_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    requested_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filters_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_of_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
