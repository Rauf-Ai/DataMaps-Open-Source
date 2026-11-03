from __future__ import annotations
from typing import Annotated, Optional, Any, Dict, List, Literal, Union, Callable

import logging
from datetime import datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    BillingPayment,
    BillingPaymentKind,
    Package,
    Transaction,
    TransactionStatus,
    User,
)
from app.services.email_service import send_payment_success_email
from app.services.payment_service import create_payment, fetch_payment
from app.services.subscription_service import (
    PLAN_CATALOG,
    activate_paid_plan,
    get_or_create_subscription,
    quota_snapshot,
)
from app.timeutils import utcnow


settings = get_settings()
log = logging.getLogger("payments")
router = APIRouter(prefix="/api", tags=["payments"])


def require_payments_enabled() -> None:
    if settings.local_mode:
        raise HTTPException(status_code=404, detail="Платежи отключены в локальном режиме")


@router.get("/subscriptions/plans")
async def subscription_plans():
    if settings.local_mode:
        return [{"code": "free", "name": "Локально · бесплатно", "price_rub": 0,
                 "organizations": 0, "reviews": 0, "enrichment": 0,
                 "seasonality": 0, "social": 0, "concurrent_tasks": 3,
                 "queue": "Локально"}]
    return [
        {
            "code": plan.code,
            "name": plan.name,
            "price_rub": plan.price_kop / 100,
            "organizations": plan.organizations,
            "reviews": plan.reviews,
            "enrichment": plan.enrichment,
            "seasonality": plan.seasonality,
            "social": plan.social,
            "concurrent_tasks": plan.concurrent_tasks,
            "queue": ("Приоритетная" if plan.queue_priority >= 20 else
                      "Ускоренная" if plan.queue_priority >= 10 else "Обычная"),
        }
        for plan in PLAN_CATALOG.values()
    ]


@router.get("/subscriptions/me")
async def my_subscription(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    sub = get_or_create_subscription(db, user.id)
    db.commit()
    return quota_snapshot(sub, user)


@router.post("/subscriptions/checkout")
async def subscription_checkout(
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    require_payments_enabled()
    plan_code = str(payload.get("plan_code") or "").lower()
    plan = PLAN_CATALOG.get(plan_code)
    if not plan or plan.price_kop <= 0:
        raise HTTPException(status_code=400, detail="Выберите платный тариф")
    billing = BillingPayment(
        user_id=user.id,
        kind=BillingPaymentKind.subscription,
        plan_code=plan.code,
        amount_kop=plan.price_kop,
        org_units=plan.organizations,
        status="pending",
    )
    db.add(billing)
    db.commit()
    db.refresh(billing)
    try:
        result = create_payment(
            amount_kop=plan.price_kop,
            description=f"Подписка DataMaps «{plan.name}» на 1 месяц",
            return_url=settings.yookassa_return_url,
            metadata={"billing_payment_id": str(billing.id), "kind": "subscription"},
            save_payment_method=settings.yookassa_recurring_enabled,
        )
    except RuntimeError as exc:
        billing.status = "failed"
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        billing.status = "failed"
        db.commit()
        log.exception("subscription checkout failed")
        raise HTTPException(status_code=502, detail=f"Платёжная система вернула ошибку: {exc}")
    if not result.get("confirmation_url"):
        billing.status = "failed"
        db.commit()
        raise HTTPException(status_code=502, detail="Платёжная система не вернула ссылку на оплату")
    billing.yookassa_payment_id = result["id"]
    db.commit()
    return {"payment_id": billing.id, "confirmation_url": result["confirmation_url"]}


@router.post("/subscriptions/topup")
async def subscription_topup(
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    require_payments_enabled()
    org_units = int(payload.get("organizations") or 0)
    if org_units < 100:
        raise HTTPException(status_code=400, detail="Минимальная докупка — 100 организаций")
    sub = get_or_create_subscription(db, user.id)
    if sub.plan_code not in ("pro", "business") or sub.status.value != "active":
        raise HTTPException(status_code=403, detail="Докупка доступна только на активном платном тарифе")
    amount_kop = org_units * 25
    billing = BillingPayment(
        user_id=user.id,
        kind=BillingPaymentKind.topup,
        org_units=org_units,
        amount_kop=amount_kop,
        status="pending",
    )
    db.add(billing)
    db.commit()
    db.refresh(billing)
    try:
        result = create_payment(
            amount_kop=amount_kop,
            description=f"DataMaps: дополнительно {org_units} организаций",
            return_url=settings.yookassa_return_url,
            metadata={"billing_payment_id": str(billing.id), "kind": "topup"},
        )
    except Exception as exc:  # noqa: BLE001
        billing.status = "failed"
        db.commit()
        log.exception("topup checkout failed")
        raise HTTPException(status_code=502, detail=f"Платёжная система вернула ошибку: {exc}")
    if not result.get("confirmation_url"):
        billing.status = "failed"
        db.commit()
        raise HTTPException(status_code=502, detail="Платёжная система не вернула ссылку на оплату")
    billing.yookassa_payment_id = result["id"]
    db.commit()
    return {
        "payment_id": billing.id,
        "amount_rub": amount_kop / 100,
        "confirmation_url": result["confirmation_url"],
    }


@router.post("/subscriptions/cancel")
async def cancel_subscription(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    sub = get_or_create_subscription(db, user.id)
    sub.cancel_at_period_end = True
    db.commit()
    return {"ok": True, "active_until": sub.period_end.isoformat()}


@router.delete("/subscriptions/payment-method")
async def detach_subscription_payment_method(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    sub = get_or_create_subscription(db, user.id)
    detached = bool(sub.payment_method_id)
    sub.payment_method_id = None
    sub.cancel_at_period_end = True
    db.commit()
    log.info("payment method detached user_id=%s detached=%s", user.id, detached)
    return {"ok": True, "detached": detached, "automatic_renewal": False}


@router.get("/packages")
async def list_packages(db: Annotated[Session, Depends(get_db)]):
    if settings.local_mode:
        return []
    rows = db.execute(
        select(Package).where(Package.is_active == True).order_by(Package.sort_order, Package.price_kop)  # noqa: E712
    ).scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "credits": p.credits,
            "price_rub": p.price_kop / 100,
            "price_per_credit_rub": round(p.price_kop / 100 / p.credits, 2),
        }
        for p in rows
    ]


@router.post("/payments/checkout")
async def checkout(
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    require_payments_enabled()
    package_id = int(payload.get("package_id") or 0)
    pkg = db.get(Package, package_id)
    if not pkg or not pkg.is_active:
        raise HTTPException(status_code=404, detail="Пакет не найден")

    tx = Transaction(
        user_id=user.id,
        package_id=pkg.id,
        amount_kop=pkg.price_kop,
        credits=pkg.credits,
        status=TransactionStatus.pending,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    try:
        result = create_payment(
            amount_kop=pkg.price_kop,
            description=f"Пакет «{pkg.name}» — {pkg.credits} кредитов",
            return_url=settings.yookassa_return_url,
            metadata={"transaction_id": str(tx.id), "user_id": str(user.id), "package_id": str(pkg.id)},
        )
    except RuntimeError as exc:
        log.warning("checkout: YooKassa не настроена: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("checkout: ошибка создания платежа в YooKassa")
        raise HTTPException(status_code=502, detail=f"Платёжная система вернула ошибку: {exc}")

    log.info("checkout: payment id=%s status=%s url=%s",
             result.get("id"), result.get("status"), bool(result.get("confirmation_url")))
    if not result.get("confirmation_url"):
        raise HTTPException(status_code=502,
                            detail="Платёжная система не вернула ссылку на оплату")

    tx.yookassa_payment_id = result["id"]
    db.commit()
    return {"transaction_id": tx.id, "confirmation_url": result["confirmation_url"]}


@router.post("/payments/webhook")
async def webhook(
    request: Request,
    background: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    require_payments_enabled()
    body = await request.json()
    event = body.get("event")
    obj = body.get("object") or {}
    payment_id = obj.get("id")
    if not payment_id:
        raise HTTPException(status_code=400, detail="Нет id платежа")

    # Доверяем не телу, а тянем актуальный статус из ЮКассы.
    try:
        actual = fetch_payment(payment_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("YooKassa fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Не удалось проверить статус платежа")

    billing = db.execute(
        select(BillingPayment).where(BillingPayment.yookassa_payment_id == payment_id)
    ).scalar_one_or_none()
    if billing:
        if billing.status == "succeeded":
            return {"ok": True, "duplicate": True}
        if actual["amount_kop"] != billing.amount_kop:
            log.error("Billing amount mismatch id=%s expected=%s actual=%s",
                      billing.id, billing.amount_kop, actual["amount_kop"])
            raise HTTPException(status_code=400, detail="Сумма платежа не совпадает")
        if actual["status"] == "succeeded" and actual.get("paid"):
            billed_user = db.get(User, billing.user_id)
            if not billed_user:
                raise HTTPException(status_code=404, detail="Пользователь платежа не найден")
            if billing.kind == BillingPaymentKind.subscription:
                sub = get_or_create_subscription(db, billed_user.id)
                activate_paid_plan(sub, billing.plan_code or "")
                if actual.get("payment_method_saved"):
                    sub.payment_method_id = actual.get("payment_method_id")
                    sub.cancel_at_period_end = False
                else:
                    sub.payment_method_id = None
                    sub.cancel_at_period_end = True
            else:
                billed_user.credits_balance += billing.org_units
            billed_user.total_paid_kop += billing.amount_kop
            billing.status = "succeeded"
            billing.completed_at = utcnow()
            db.commit()
            plan = PLAN_CATALOG.get(billing.plan_code or "")
            background.add_task(
                send_payment_success_email,
                billed_user.email,
                plan.name if plan else "Дополнительный объём",
                billing.org_units,
                billing.amount_kop / 100,
            )
        elif actual["status"] in ("canceled", "failed"):
            billing.status = actual["status"]
            billing.completed_at = utcnow()
            db.commit()
        return {"ok": True}

    tx = db.execute(
        select(Transaction).where(Transaction.yookassa_payment_id == payment_id)
    ).scalar_one_or_none()
    if not tx:
        log.warning("Webhook: transaction not found for payment_id=%s", payment_id)
        return {"ok": True}

    # Idempotency
    if tx.status == TransactionStatus.succeeded:
        return {"ok": True, "duplicate": True}

    if actual["status"] == "succeeded" and actual.get("paid"):
        user = db.get(User, tx.user_id)
        if user:
            user.credits_balance += tx.credits
            user.total_paid_kop += tx.amount_kop
        tx.status = TransactionStatus.succeeded
        tx.completed_at = utcnow()
        db.commit()
        if user:
            pkg = db.get(Package, tx.package_id)
            background.add_task(
                send_payment_success_email,
                user.email,
                pkg.name if pkg else "Пакет",
                tx.credits,
                tx.amount_kop / 100,
            )
    elif actual["status"] in ("canceled", "failed"):
        tx.status = TransactionStatus.canceled if actual["status"] == "canceled" else TransactionStatus.failed
        tx.completed_at = utcnow()
        db.commit()
    else:
        log.info("Webhook event=%s status=%s — пропускаем", event, actual["status"])
    return {"ok": True}
