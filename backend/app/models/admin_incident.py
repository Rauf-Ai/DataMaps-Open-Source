from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AdminIncident(Base):
    """Operational incident recorded for the admin control center."""

    __tablename__ = "admin_incidents"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_admin_incidents_severity",
        ),
        CheckConstraint(
            "status IN ('new', 'acknowledged', 'resolved')",
            name="ck_admin_incidents_status",
        ),
        Index("ix_admin_incidents_status_severity_last_seen", "status", "severity", "last_seen"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    task_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    acknowledged_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolved_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
