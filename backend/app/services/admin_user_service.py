from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

"""Explicitly allowlisted user-360 data for administrators."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AdminAuditLog,
    AiRequest,
    BillingPayment,
    SupportConversation,
    Transaction,
    User,
    UserSubscription,
)
from app.services.subscription_service import PLAN_CATALOG, quota_snapshot


_LIMIT = 20


def _iso(value):
    return value.isoformat() if value else None


def _subscription_snapshot(db: Session, user: User) -> tuple[dict, dict]:
    sub = db.execute(select(UserSubscription).where(UserSubscription.user_id == user.id)).scalar_one_or_none()
    if sub is None:
        plan = PLAN_CATALOG["free"]
        quotas = {
            "organizations": {
                "limit": plan.organizations, "used": 0, "extra": int(user.credits_balance or 0),
                "available": plan.organizations + int(user.credits_balance or 0),
            },
            "reviews": {"limit": plan.reviews, "used": 0, "extra": 0, "available": plan.reviews},
            "enrichment": {"limit": plan.enrichment, "used": 0, "extra": 0, "available": plan.enrichment},
            "seasonality": {"limit": plan.seasonality, "used": 0, "extra": 0, "available": plan.seasonality},
            "social": {"limit": plan.social, "used": 0, "extra": 0, "available": plan.social},
        }
        return {
            "plan_code": plan.code, "plan_name": plan.name, "status": "active",
            "period_start": None, "period_end": None, "cancel_at_period_end": False,
            "payment_method_attached": False,
        }, quotas
    raw = quota_snapshot(sub, user)
    subscription = {
        "plan_code": raw["plan"]["code"], "plan_name": raw["plan"]["name"],
        "status": raw["status"], "period_start": raw["period_start"],
        "period_end": raw["period_end"], "cancel_at_period_end": raw["cancel_at_period_end"],
        "payment_method_attached": raw["payment_method_attached"],
    }
    return subscription, raw["quotas"]


def get_user_360(db: Session, user_id: int) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise LookupError("Пользователь не найден")
    subscription, quotas = _subscription_snapshot(db, user)
    ai_requests = db.execute(
        select(AiRequest).where(AiRequest.user_id == user.id).order_by(AiRequest.created_at.desc()).limit(_LIMIT)
    ).scalars().all()
    billing = db.execute(
        select(BillingPayment).where(BillingPayment.user_id == user.id)
        .order_by(BillingPayment.created_at.desc()).limit(_LIMIT)
    ).scalars().all()
    transactions = db.execute(
        select(Transaction).where(Transaction.user_id == user.id)
        .order_by(Transaction.created_at.desc()).limit(_LIMIT)
    ).scalars().all()
    conversations = db.execute(
        select(SupportConversation).where(SupportConversation.user_id == user.id)
        .order_by(SupportConversation.updated_at.desc()).limit(_LIMIT)
    ).scalars().all()
    audits = db.execute(
        select(AdminAuditLog).where(
            AdminAuditLog.target_type == "user", AdminAuditLog.target_id == str(user.id)
        ).order_by(AdminAuditLog.created_at.desc()).limit(_LIMIT)
    ).scalars().all()
    payments = [
        {
            "source": "billing", "id": payment.id, "kind": payment.kind.value,
            "plan_code": payment.plan_code, "org_units": payment.org_units,
            "amount_kop": payment.amount_kop, "status": payment.status,
            "is_recurring": bool(payment.is_recurring), "created_at": _iso(payment.created_at),
            "completed_at": _iso(payment.completed_at),
        }
        for payment in billing
    ] + [
        {
            "source": "legacy", "id": transaction.id, "credits": transaction.credits,
            "amount_kop": transaction.amount_kop,
            "status": transaction.status.value, "created_at": _iso(transaction.created_at),
            "completed_at": _iso(transaction.completed_at),
        }
        for transaction in transactions
    ]
    payments.sort(key=lambda item: item["created_at"] or "", reverse=True)
    return {
        "profile": {
            "id": user.id, "email": user.email, "is_admin": bool(user.is_admin),
            "is_active": bool(user.is_active), "is_blocked": bool(user.is_blocked),
            "email_verified": user.email_verified_at is not None,
            "credits_balance": user.credits_balance, "credits_spent": user.credits_spent,
            "total_paid_kop": user.total_paid_kop, "created_at": _iso(user.created_at),
        },
        "subscription": subscription,
        "quotas": quotas,
        "recent_activity": [
            {"type": "ai_request", "id": request.id, "created_at": _iso(request.created_at)}
            for request in ai_requests
        ],
        "payments": payments[:_LIMIT],
        "support_conversations": [
            {"id": convo.id, "status": convo.status, "created_at": _iso(convo.created_at),
             "updated_at": _iso(convo.updated_at), "last_message_at": _iso(convo.last_message_at)}
            for convo in conversations
        ],
        "audit_entries": [
            {"id": audit.id, "admin_id": audit.admin_id, "action": audit.action,
             "target_type": audit.target_type, "target_id": audit.target_id,
             "comment": audit.comment, "request_id": audit.request_id,
             "created_at": _iso(audit.created_at)}
            for audit in audits
        ],
        "limits": {
            "recent_activity": _LIMIT, "payments": _LIMIT,
            "support_conversations": _LIMIT, "audit_entries": _LIMIT,
        },
    }


def list_user_audit(db: Session, user_id: int, *, page: int, page_size: int) -> tuple[list[dict], int]:
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("Некорректная пагинация")
    user = db.get(User, user_id)
    if user is None:
        raise LookupError("Пользователь не найден")
    base = select(AdminAuditLog).where(
        AdminAuditLog.target_type == "user", AdminAuditLog.target_id == str(user_id)
    )
    total = int(db.scalar(
        select(func.count()).select_from(AdminAuditLog).where(
            AdminAuditLog.target_type == "user", AdminAuditLog.target_id == str(user_id)
        )
    ) or 0)
    rows = db.execute(
        base.order_by(AdminAuditLog.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
    ).scalars().all()
    return [
        {
            "id": row.id, "admin_id": row.admin_id, "action": row.action,
            "target_type": row.target_type, "target_id": row.target_id,
            "comment": row.comment, "request_id": row.request_id,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ], total
