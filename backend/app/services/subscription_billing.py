from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import logging

from sqlalchemy import select

from app.db import SessionLocal
from app.models import BillingPayment, BillingPaymentKind, SubscriptionStatus, UserSubscription
from app.services.payment_service import create_payment
from app.services.subscription_service import PLAN_CATALOG
from app.timeutils import utcnow


log = logging.getLogger(__name__)


def renew_due_subscriptions() -> None:
    """Создаёт автоплатёж для истёкших платных подписок с сохранённым способом оплаты.

    Успешное продление активируется только webhook-ом после проверки платежа в ЮKassa.
    """
    db = SessionLocal()
    try:
        now = utcnow()
        rows = db.execute(
            select(UserSubscription).where(
                UserSubscription.plan_code.in_(("pro", "business")),
                UserSubscription.status == SubscriptionStatus.active,
                UserSubscription.period_end <= now,
                UserSubscription.cancel_at_period_end.is_(False),
            )
        ).scalars().all()
        for sub in rows:
            pending = db.execute(
                select(BillingPayment.id).where(
                    BillingPayment.user_id == sub.user_id,
                    BillingPayment.kind == BillingPaymentKind.subscription,
                    BillingPayment.is_recurring.is_(True),
                    BillingPayment.status == "pending",
                ).limit(1)
            ).first()
            if pending:
                continue
            plan = PLAN_CATALOG.get(sub.plan_code)
            if not plan or not sub.payment_method_id:
                sub.status = SubscriptionStatus.past_due
                continue
            billing = BillingPayment(
                user_id=sub.user_id,
                kind=BillingPaymentKind.subscription,
                plan_code=plan.code,
                org_units=plan.organizations,
                amount_kop=plan.price_kop,
                status="pending",
                is_recurring=True,
            )
            db.add(billing)
            db.commit()
            db.refresh(billing)
            try:
                payment = create_payment(
                    amount_kop=plan.price_kop,
                    description=f"Продление подписки DataMaps «{plan.name}»",
                    return_url="",
                    metadata={"billing_payment_id": str(billing.id), "kind": "subscription_renewal"},
                    payment_method_id=sub.payment_method_id,
                )
                billing.yookassa_payment_id = payment["id"]
            except Exception:  # noqa: BLE001
                billing.status = "failed"
                sub.status = SubscriptionStatus.past_due
                log.exception("subscription renewal failed user_id=%s", sub.user_id)
            db.commit()
    finally:
        db.close()
