from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PendingAction(Base):
    """Действие агента, ожидающее подтверждения владельца в Telegram.

    kind:   grant_credits | block_user | unblock_user.
    status: pending | executed | rejected | failed.
    payload_json — параметры действия (например, {"delta": 50, "comment": "..."}).
    """
    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    summary: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
