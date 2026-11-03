from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.subscription import SubscriptionStatus, UserSubscription
from app.config import get_settings
from app.timeutils import utcnow


@dataclass(frozen=True)
class PlanDefinition:
    code: str
    name: str
    price_kop: int
    organizations: int
    reviews: int
    enrichment: int
    seasonality: int
    social: int
    concurrent_tasks: int
    queue_priority: int


PLAN_CATALOG = {
    "free": PlanDefinition("free", "Бесплатно", 0, 50, 10, 10, 10, 3, 1, 0),
    "pro": PlanDefinition("pro", "Про", 90_000, 2_000, 2_000, 2_000, 20, 100, 2, 10),
    "business": PlanDefinition("business", "Бизнес", 150_000, 5_000, 5_000, 5_000, 100, 300, 3, 20),
}
LOCAL_QUOTA = 1_000_000_000

_USED_FIELD = {
    "organizations": "org_used",
    "reviews": "reviews_used",
    "enrichment": "enrichment_used",
    "seasonality": "seasonality_used",
    "social": "social_used",
}
_RU_LABEL = {
    "organizations": "организаций",
    "reviews": "анализа отзывов",
    "enrichment": "обогащения",
    "seasonality": "анализа сезонности",
    "social": "анализа соцсетей",
}


def _next_calendar_month(now: datetime) -> datetime:
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def get_or_create_subscription(db: Session, user_id: int, *, now: Optional[datetime] = None) -> UserSubscription:
    now = now or utcnow()
    sub = db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user_id)
    ).scalar_one_or_none()
    if sub is None:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        sub = UserSubscription(
            user_id=user_id,
            plan_code="free",
            status=SubscriptionStatus.active,
            period_start=start,
            period_end=_next_calendar_month(now),
        )
        db.add(sub)
        db.flush()
    sync_subscription_period(sub, now=now)
    return sub


def sync_subscription_period(sub: UserSubscription, *, now: Optional[datetime] = None) -> None:
    now = now or utcnow()
    if now < sub.period_end:
        return

    old_plan = PLAN_CATALOG.get(sub.plan_code, PLAN_CATALOG["free"])
    unused_orgs = max(0, old_plan.organizations + sub.org_rollover - sub.org_used)
    if sub.plan_code != "free" and sub.status == SubscriptionStatus.active:
        sub.plan_code = "free"
        sub.status = SubscriptionStatus.expired
        sub.org_rollover = 0
    else:
        sub.org_rollover = min(unused_orgs, old_plan.organizations)

    sub.period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sub.period_end = _next_calendar_month(now)
    sub.org_used = 0
    sub.reviews_used = 0
    sub.enrichment_used = 0
    sub.seasonality_used = 0
    sub.social_used = 0


def activate_paid_plan(sub: UserSubscription, plan_code: str, *, now: Optional[datetime] = None) -> None:
    if plan_code not in ("pro", "business"):
        raise ValueError("Можно активировать только платный тариф")
    now = now or utcnow()
    old_plan = PLAN_CATALOG.get(sub.plan_code, PLAN_CATALOG["free"])
    sub.org_rollover = min(
        max(0, old_plan.organizations + sub.org_rollover - sub.org_used),
        old_plan.organizations,
    )
    sub.plan_code = plan_code
    sub.status = SubscriptionStatus.active
    sub.cancel_at_period_end = False
    sub.period_start = now
    sub.period_end = now + timedelta(days=30)
    sub.org_used = 0
    sub.reviews_used = 0
    sub.enrichment_used = 0
    sub.seasonality_used = 0
    sub.social_used = 0


def assign_plan_manually(
    sub: UserSubscription, plan_code: str, *, now: Optional[datetime] = None
) -> None:
    """Assign a plan without creating a payment or enabling automatic renewal."""
    if plan_code not in PLAN_CATALOG:
        raise ValueError("Неизвестный тариф")

    now = now or utcnow()
    if plan_code in ("pro", "business"):
        activate_paid_plan(sub, plan_code, now=now)
        sub.cancel_at_period_end = True
    else:
        sub.plan_code = "free"
        sub.status = SubscriptionStatus.active
        sub.period_start = now
        sub.period_end = now + timedelta(days=30)
        sub.org_rollover = 0
        sub.org_used = 0
        sub.reviews_used = 0
        sub.enrichment_used = 0
        sub.seasonality_used = 0
        sub.social_used = 0
        sub.cancel_at_period_end = False

    # A manual assignment must never charge a previously saved payment method.
    sub.payment_method_id = None


def quota_available(sub: UserSubscription, user, kind: str) -> int:
    if get_settings().local_mode:
        return LOCAL_QUOTA
    plan = PLAN_CATALOG.get(sub.plan_code, PLAN_CATALOG["free"])
    if kind == "organizations":
        monthly = max(0, plan.organizations + sub.org_rollover - sub.org_used)
        return monthly + max(0, int(user.credits_balance or 0))
    field = _USED_FIELD[kind]
    return max(0, int(getattr(plan, kind)) - int(getattr(sub, field)))


def spend_quota(sub: UserSubscription, user, kind: str, amount: int) -> dict[str, int]:
    amount = int(amount)
    if amount <= 0:
        return {"monthly": 0, "extra": 0}
    if get_settings().local_mode:
        return {"monthly": 0, "extra": 0}
    available = quota_available(sub, user, kind)
    if available < amount:
        raise ValueError(f"Недостаточно квоты {_RU_LABEL[kind]}: нужно {amount}, доступно {available}")

    field = _USED_FIELD[kind]
    if kind != "organizations":
        setattr(sub, field, int(getattr(sub, field)) + amount)
        return {"monthly": amount, "extra": 0}

    plan = PLAN_CATALOG.get(sub.plan_code, PLAN_CATALOG["free"])
    monthly_left = max(0, plan.organizations + sub.org_rollover - sub.org_used)
    from_monthly = min(monthly_left, amount)
    from_extra = amount - from_monthly
    sub.org_used += from_monthly
    if from_extra:
        user.credits_balance -= from_extra
        user.credits_spent += from_extra
    return {"monthly": from_monthly, "extra": from_extra}


def quota_snapshot(sub: UserSubscription, user) -> dict:
    if get_settings().local_mode:
        quotas = {
            kind: {"limit": LOCAL_QUOTA, "used": 0, "extra": 0, "available": LOCAL_QUOTA}
            for kind in _USED_FIELD
        }
        return {
            "local_mode": True,
            "plan": {**asdict(PLAN_CATALOG["free"]), "name": "Локально · бесплатно"},
            "status": "active",
            "period_start": sub.period_start.isoformat(),
            "period_end": sub.period_end.isoformat(),
            "cancel_at_period_end": False,
            "payment_method_attached": False,
            "quotas": quotas,
        }
    plan = PLAN_CATALOG.get(sub.plan_code, PLAN_CATALOG["free"])
    quotas = {}
    for kind, field in _USED_FIELD.items():
        limit = plan.organizations + sub.org_rollover if kind == "organizations" else getattr(plan, kind)
        used = int(getattr(sub, field))
        quotas[kind] = {
            "limit": int(limit),
            "used": used,
            "extra": int(user.credits_balance or 0) if kind == "organizations" else 0,
            "available": quota_available(sub, user, kind),
        }
    return {
        "plan": asdict(plan),
        "status": sub.status.value if hasattr(sub.status, "value") else str(sub.status),
        "period_start": sub.period_start.isoformat(),
        "period_end": sub.period_end.isoformat(),
        "cancel_at_period_end": bool(getattr(sub, "cancel_at_period_end", False)),
        "payment_method_attached": bool(getattr(sub, "payment_method_id", None)),
        "quotas": quotas,
    }


def active_task_count(db: Session, user_id: int) -> int:
    """Count all user jobs occupying a subscription concurrency slot."""
    # Local imports avoid a model import cycle during application startup.
    from app.models import CompanyAnalysisTask, ExportStatus, ExportTask, ParserTask, ReviewTask, SocialAnalysisTask

    active = (ExportStatus.queued, ExportStatus.running)
    models = (ParserTask, ReviewTask, SocialAnalysisTask, CompanyAnalysisTask, ExportTask)
    return sum(
        int(db.scalar(
            select(func.count()).select_from(model).where(
                model.user_id == user_id,
                model.status.in_(active),
            )
        ) or 0)
        for model in models
    )


def ensure_task_slot(db: Session, sub: UserSubscription, user_id: int) -> dict[str, int]:
    """Raise when the current plan has no free background-task slot."""
    limit = 3 if get_settings().local_mode else PLAN_CATALOG.get(sub.plan_code, PLAN_CATALOG["free"]).concurrent_tasks
    active = active_task_count(db, user_id)
    if active >= limit:
        raise ValueError(
            f"Лимит одновременных задач исчерпан: выполняется {active} из {limit}. "
            "Дождитесь завершения текущей задачи или смените тариф."
        )
    return {"active": active, "limit": limit, "available": limit - active}
