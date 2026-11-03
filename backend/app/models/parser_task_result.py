from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ParserTaskResult(Base):
    """A transient, cursor-addressable card snapshot emitted by a parser task."""

    __tablename__ = "parser_task_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("parser_tasks.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    identity_key: Mapped[str] = mapped_column(String(600), nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("task_id", "identity_key", name="uq_parser_task_result_identity"),
        UniqueConstraint("task_id", "position", name="uq_parser_task_result_position"),
    )
