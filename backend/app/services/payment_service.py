from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import uuid

from yookassa import Configuration, Payment

from app.config import get_settings


settings = get_settings()


def _ensure_configured() -> None:
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise RuntimeError("YooKassa не настроена: задайте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY в .env")
    Configuration.account_id = settings.yookassa_shop_id
    Configuration.secret_key = settings.yookassa_secret_key


def create_payment(
    amount_kop: int,
    description: str,
    return_url: str,
    metadata: dict,
    *,
    save_payment_method: bool = False,
    payment_method_id: Optional[str] = None,
) -> dict:
    _ensure_configured()
    rub = f"{amount_kop / 100:.2f}"
    payload = {
            "amount": {"value": rub, "currency": "RUB"},
            "capture": True,
            "description": description,
            "metadata": metadata,
        }
    if payment_method_id:
        payload["payment_method_id"] = payment_method_id
    else:
        payload["confirmation"] = {"type": "redirect", "return_url": return_url}
        if save_payment_method:
            payload["save_payment_method"] = True
    payment = Payment.create(payload, str(uuid.uuid4()))
    return {
        "id": payment.id,
        "status": payment.status,
        "confirmation_url": payment.confirmation.confirmation_url if payment.confirmation else None,
    }


def fetch_payment(payment_id: str) -> dict:
    _ensure_configured()
    p = Payment.find_one(payment_id)
    md = getattr(p, "metadata", None) or {}
    amount_value = float(p.amount.value) if p.amount else 0.0
    method = getattr(p, "payment_method", None)
    return {
        "id": p.id,
        "status": p.status,
        "paid": getattr(p, "paid", False),
        "amount_kop": int(round(amount_value * 100)),
        "metadata": md,
        "payment_method_id": getattr(method, "id", None),
        "payment_method_saved": bool(getattr(method, "saved", False)),
    }
