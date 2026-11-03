from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import BillingPayment, BillingPaymentKind, User, UserSubscription
from app.routers import payments
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
        db.add(User(id=1, email="buyer@example.com", password_hash="x", email_verified_at=utcnow()))
        db.commit()

    app = FastAPI()
    app.include_router(payments.router)

    def override_db():
        with session_factory() as db:
            yield db

    def override_user():
        with session_factory() as db:
            return db.get(User, 1)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return TestClient(app), session_factory, engine


def test_subscription_checkout_does_not_request_card_saving_when_recurring_is_disabled(monkeypatch):
    client, _session_factory, engine = _client()
    monkeypatch.setattr(payments.settings, "yookassa_recurring_enabled", False, raising=False)

    with patch(
        "app.routers.payments.create_payment",
        return_value={"id": "pay-1", "status": "pending", "confirmation_url": "https://pay.example/1"},
    ) as create:
        response = client.post("/api/subscriptions/checkout", json={"plan_code": "pro"})

    assert response.status_code == 200
    assert response.json()["confirmation_url"] == "https://pay.example/1"
    assert create.call_args.kwargs["save_payment_method"] is False
    engine.dispose()


def test_one_time_subscription_payment_disables_automatic_renewal():
    client, session_factory, engine = _client()
    with session_factory() as db:
        db.add(BillingPayment(
            user_id=1,
            kind=BillingPaymentKind.subscription,
            plan_code="pro",
            org_units=2_000,
            amount_kop=90_000,
            status="pending",
            yookassa_payment_id="pay-2",
        ))
        db.commit()

    actual = {
        "id": "pay-2",
        "status": "succeeded",
        "paid": True,
        "amount_kop": 90_000,
        "payment_method_id": None,
        "payment_method_saved": False,
    }
    with patch("app.routers.payments.fetch_payment", return_value=actual), patch(
        "app.routers.payments.send_payment_success_email", new=AsyncMock()
    ):
        response = client.post("/api/payments/webhook", json={"event": "payment.succeeded", "object": {"id": "pay-2"}})

    assert response.status_code == 200
    with session_factory() as db:
        subscription = db.execute(select(UserSubscription).where(UserSubscription.user_id == 1)).scalar_one()
        assert subscription.plan_code == "pro"
        assert subscription.cancel_at_period_end is True
        assert subscription.payment_method_id is None
    engine.dispose()


def test_subscription_status_exposes_attachment_without_payment_method_id():
    client, session_factory, engine = _client()
    with session_factory() as db:
        subscription = get_or_create_subscription(db, 1)
        subscription.payment_method_id = "pm-secret-value"
        subscription.cancel_at_period_end = False
        db.commit()

    response = client.get("/api/subscriptions/me")

    assert response.status_code == 200
    body = response.json()
    assert body["payment_method_attached"] is True
    assert "pm-secret-value" not in response.text
    engine.dispose()


def test_user_can_detach_saved_payment_method():
    client, session_factory, engine = _client()
    with session_factory() as db:
        subscription = get_or_create_subscription(db, 1)
        subscription.payment_method_id = "pm-to-detach"
        subscription.cancel_at_period_end = False
        db.commit()

    response = client.delete("/api/subscriptions/payment-method")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "detached": True, "automatic_renewal": False}
    with session_factory() as db:
        subscription = db.execute(select(UserSubscription).where(UserSubscription.user_id == 1)).scalar_one()
        assert subscription.payment_method_id is None
        assert subscription.cancel_at_period_end is True
    engine.dispose()
