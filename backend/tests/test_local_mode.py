"""Local edition keeps the core workflow free and payment endpoints inert."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import User
from app.routers import export, payments
from app.services import subscription_service
from app.timeutils import utcnow


def test_local_mode_allows_export_without_credits_and_disables_checkout(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id=1, email="local@example.com", password_hash="x", credits_balance=0,
                    email_verified_at=utcnow()))
        db.commit()

    app = FastAPI()
    app.include_router(export.router)
    app.include_router(payments.router)

    def override_db():
        with factory() as db:
            yield db

    def override_user():
        with factory() as db:
            return db.get(User, 1)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    monkeypatch.setattr(export.settings, "local_mode", True)
    monkeypatch.setattr(payments.settings, "local_mode", True)

    with patch.object(export, "run_export"):
        client = TestClient(app)
        response = client.post("/api/export", json={"filters": {}, "limit": 3})
        assert response.status_code == 200, response.json()
        assert response.json()["requested_rows"] == 3

        snapshot = client.get("/api/subscriptions/me").json()
        assert snapshot["local_mode"] is True
        assert snapshot["quotas"]["organizations"]["available"] > 3
        assert client.post("/api/subscriptions/checkout", json={"plan_code": "pro"}).status_code == 404
        assert client.post("/api/payments/checkout", json={"package_id": 1}).status_code == 404
        assert client.post("/api/payments/webhook", json={"event": "payment.succeeded"}).status_code == 404

    with factory() as db:
        user = db.get(User, 1)
        sub = subscription_service.get_or_create_subscription(db, user.id)
        assert subscription_service.spend_quota(sub, user, "organizations", 500) == {"monthly": 0, "extra": 0}
        assert user.credits_balance == 0
    engine.dispose()
