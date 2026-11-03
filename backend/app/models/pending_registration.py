from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PendingRegistration(Base):
    """Заявка на регистрацию до подтверждения email.

    Реальный User НЕ создаётся, пока пользователь не перейдёт по ссылке из письма —
    поэтому неподтверждённый email не блокирует повторную регистрацию.
    """
    __tablename__ = "pending_registrations"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
