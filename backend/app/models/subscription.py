from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    past_due = "past_due"
    canceled = "canceled"
    expired = "expired"


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False, index=True)
    plan_code: Mapped[str] = mapped_column(String(24), default="free", nullable=False, index=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus), default=SubscriptionStatus.active, nullable=False, index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    org_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    org_rollover: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reviews_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enrichment_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    seasonality_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    social_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payment_method_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BillingPaymentKind(str, enum.Enum):
    subscription = "subscription"
    topup = "topup"


class BillingPayment(Base):
    __tablename__ = "billing_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    kind: Mapped[BillingPaymentKind] = mapped_column(Enum(BillingPaymentKind), nullable=False, index=True)
    plan_code: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    org_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_kop: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    yookassa_payment_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
