from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.services import subscription_service
from app.services.subscription_service import PLAN_CATALOG, ensure_task_slot, quota_snapshot, spend_quota
from app.timeutils import utcnow


def _subscription(plan="free", **overrides):
    values = {
        "plan_code": plan,
        "status": "active",
        "period_start": utcnow(),
        "period_end": utcnow() + timedelta(days=30),
        "org_used": 0,
        "org_rollover": 0,
        "reviews_used": 0,
        "enrichment_used": 0,
        "seasonality_used": 0,
        "social_used": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_catalog_matches_approved_tariffs():
    assert PLAN_CATALOG["free"].organizations == 50
    assert PLAN_CATALOG["pro"].price_kop == 90_000
    assert PLAN_CATALOG["pro"].organizations == 2_000
    assert PLAN_CATALOG["business"].price_kop == 150_000
    assert PLAN_CATALOG["business"].organizations == 5_000
    assert PLAN_CATALOG["business"].social == 300


def test_org_quota_uses_monthly_then_permanent_balance():
    sub = _subscription("pro", org_used=1_990)
    user = SimpleNamespace(credits_balance=25, credits_spent=0)

    charged = spend_quota(sub, user, "organizations", 20)

    assert charged == {"monthly": 10, "extra": 10}
    assert sub.org_used == 2_000
    assert user.credits_balance == 15
    assert user.credits_spent == 10


def test_feature_quota_never_falls_back_to_org_balance():
    sub = _subscription("free", social_used=3)
    user = SimpleNamespace(credits_balance=500, credits_spent=0)

    try:
        spend_quota(sub, user, "social", 1)
    except ValueError as exc:
        assert "соцсет" in str(exc).lower()
    else:
        raise AssertionError("social quota must be enforced")


def test_snapshot_includes_extra_organizations():
    sub = _subscription("business", org_used=100, org_rollover=20, reviews_used=12)
    user = SimpleNamespace(credits_balance=45)
    snap = quota_snapshot(sub, user)
    assert snap["quotas"]["organizations"]["available"] == 4_965
    assert snap["quotas"]["reviews"]["available"] == 4_988


def test_concurrent_task_limit_is_enforced(monkeypatch):
    sub = _subscription("free")
    monkeypatch.setattr(subscription_service, "active_task_count", lambda _db, _user_id: 1)

    with pytest.raises(ValueError, match="1 из 1"):
        ensure_task_slot(object(), sub, 42)


def test_paid_plan_reports_free_task_slots(monkeypatch):
    sub = _subscription("business")
    monkeypatch.setattr(subscription_service, "active_task_count", lambda _db, _user_id: 1)

    assert ensure_task_slot(object(), sub, 42) == {"active": 1, "limit": 3, "available": 2}
