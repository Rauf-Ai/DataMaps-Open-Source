from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_admin_user
from app.models import AdminAuditLog, BillingPayment, User, UserSubscription
from app.routers import admin
from app.services.subscription_service import get_or_create_subscription
from app.timeutils import utcnow


def _client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        administrator = User(
            id=1,
            email="admin@datamaps.ru",
            password_hash="x",
            is_admin=True,
            email_verified_at=utcnow(),
        )
        customer = User(
            id=2,
            email="customer@example.com",
            password_hash="x",
            credits_balance=125,
            email_verified_at=utcnow(),
        )
        db.add_all([administrator, customer])
        db.flush()
        subscription = get_or_create_subscription(db, customer.id)
        subscription.plan_code = "pro"
        db.commit()

    app = FastAPI()

    @app.middleware("http")
    async def add_request_id(request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID")
        return await call_next(request)

    app.include_router(admin.router)

    def override_db():
        with session_factory() as db:
            yield db

    def override_admin():
        with session_factory() as db:
            return db.get(User, 1)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_admin_user] = override_admin
    return TestClient(app), session_factory, engine


def test_admin_user_list_includes_current_plan_and_period():
    client, _session_factory, engine = _client()

    response = client.get("/api/admin/users?q=customer")

    assert response.status_code == 200
    user = response.json()[0]
    assert user["plan_code"] == "pro"
    assert user["plan_name"] == "Про"
    assert user["subscription_status"] == "active"
    assert user["subscription_period_end"]
    engine.dispose()


def test_admin_can_assign_business_plan_without_creating_payment():
    client, session_factory, engine = _client()
    before = utcnow()

    response = client.post(
        "/api/admin/users/2/plan",
        json={"plan_code": "business", "comment": "VIP клиент"},
        headers={"X-Request-ID": "plan-request"},
    )

    assert response.status_code == 200
    assert response.json()["plan_code"] == "business"
    with session_factory() as db:
        subscription = db.execute(
            select(UserSubscription).where(UserSubscription.user_id == 2)
        ).scalar_one()
        assert subscription.plan_code == "business"
        assert subscription.status.value == "active"
        assert subscription.cancel_at_period_end is True
        assert subscription.payment_method_id is None
        assert before + timedelta(days=29) < subscription.period_end < before + timedelta(days=31)
        assert db.query(BillingPayment).count() == 0
        log = db.scalars(select(AdminAuditLog)).one()
        assert log.action == "user.plan.assign"
        assert json.loads(log.before_json)["plan_code"] == "pro"
        assert json.loads(log.after_json)["plan_code"] == "business"
        assert log.comment == "VIP клиент"
        assert log.request_id == "plan-request"
    engine.dispose()


def test_admin_plan_audit_snapshots_expired_persisted_plan_before_sync():
    client, session_factory, engine = _client()
    with session_factory() as db:
        subscription = db.scalar(select(UserSubscription).where(UserSubscription.user_id == 2))
        subscription.period_end = utcnow() - timedelta(days=1)
        db.commit()

    response = client.post(
        "/api/admin/users/2/plan",
        json={"plan_code": "business", "comment": "Продление вручную"},
    )

    assert response.status_code == 200
    with session_factory() as db:
        log = db.scalars(select(AdminAuditLog)).one()
        assert json.loads(log.before_json) == {"plan_code": "pro"}
        assert json.loads(log.after_json) == {"plan_code": "business"}
    engine.dispose()


def test_admin_plan_assignment_rejects_unknown_plan():
    client, _session_factory, engine = _client()

    response = client.post("/api/admin/users/2/plan", json={"plan_code": "enterprise"})

    assert response.status_code == 400
    assert "тариф" in response.json()["detail"].lower()
    engine.dispose()


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/admin/users/2/plan", {"plan_code": "business"}),
        ("/api/admin/users/2/credits", {"delta": 50}),
        ("/api/admin/users/2/block", {"blocked": True}),
    ],
)
def test_admin_user_mutations_require_a_nonempty_comment(path, payload):
    client, _session_factory, engine = _client()

    response = client.post(path, json={**payload, "comment": "   "})

    assert response.status_code == 400
    assert "комментар" in response.json()["detail"].lower()
    engine.dispose()


def test_admin_credit_change_writes_audited_balance_change():
    client, session_factory, engine = _client()

    response = client.post(
        "/api/admin/users/2/credits",
        json={"delta": -30, "comment": "Корректировка"},
        headers={"X-Request-ID": "credits-request"},
    )

    assert response.status_code == 200
    assert response.json()["credits_balance"] == 95
    with session_factory() as db:
        log = db.scalars(select(AdminAuditLog)).one()
        assert log.action == "user.credits.adjust"
        assert json.loads(log.before_json) == {"credits_balance": 125}
        assert json.loads(log.after_json) == {"credits_balance": 95, "delta": -30}
        assert log.comment == "Корректировка"
        assert log.request_id == "credits-request"
    engine.dispose()


def test_admin_block_change_writes_audited_state_change():
    client, session_factory, engine = _client()

    response = client.post(
        "/api/admin/users/2/block",
        json={"blocked": True, "comment": "Нарушение правил"},
        headers={"X-Request-ID": "block-request"},
    )

    assert response.status_code == 200
    assert response.json()["is_blocked"] is True
    with session_factory() as db:
        log = db.scalars(select(AdminAuditLog)).one()
        assert log.action == "user.block.set"
        assert json.loads(log.before_json) == {"is_blocked": False}
        assert json.loads(log.after_json) == {"is_blocked": True}
        assert log.comment == "Нарушение правил"
        assert log.request_id == "block-request"
    engine.dispose()


@pytest.mark.parametrize("audit_exception", [RuntimeError, ValueError])
@pytest.mark.parametrize(
    ("path", "payload", "assert_unchanged"),
    [
        (
            "/api/admin/users/2/plan",
            {"plan_code": "business", "comment": "Тест отката"},
            lambda db: db.scalar(select(UserSubscription.plan_code).where(UserSubscription.user_id == 2)) == "pro",
        ),
        (
            "/api/admin/users/2/credits",
            {"delta": 50, "comment": "Тест отката"},
            lambda db: db.get(User, 2).credits_balance == 125,
        ),
        (
            "/api/admin/users/2/block",
            {"blocked": True, "comment": "Тест отката"},
            lambda db: db.get(User, 2).is_blocked is False,
        ),
    ],
)
def test_admin_mutation_rolls_back_when_audit_write_fails(
    monkeypatch, audit_exception, path, payload, assert_unchanged
):
    client, session_factory, engine = _client()
    client = TestClient(client.app, raise_server_exceptions=False)

    def fail_write_audit(*args, **kwargs):
        raise audit_exception("audit unavailable")

    assert hasattr(admin, "admin_audit_service"), "admin audit service is not wired into routes"
    monkeypatch.setattr(admin.admin_audit_service, "write_audit", fail_write_audit)

    response = client.post(path, json=payload)

    assert response.status_code == 500
    with session_factory() as db:
        assert assert_unchanged(db)
        assert db.query(AdminAuditLog).count() == 0
    engine.dispose()
