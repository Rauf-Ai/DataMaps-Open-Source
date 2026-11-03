from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_admin_user, get_current_user
from app.models import (
    AdminIncident,
    AdminAuditLog,
    AiRequest,
    ExportStatus,
    ParserTask,
    SupportConversation,
    User,
)
from app.routers import admin_control
from app.timeutils import utcnow


def _client(role: str = "admin"):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = utcnow()
    with session_factory() as db:
        admin = User(id=1, email="admin@datamaps.ru", password_hash="admin-secret", is_admin=True)
        user = User(id=2, email="user@example.com", password_hash="customer-secret", credits_balance=20)
        db.add_all([admin, user])
        db.add_all([
            AiRequest(user_id=2, prompt="find clinics", created_at=now),
            SupportConversation(user_id=2, status="needs_human", last_message_at=now),
            AdminIncident(
                fingerprint="parser:PARSE-500", code="PARSE-500", source="parser",
                severity="critical", status="new", message="[PARSE-500] provider failure token=hidden",
                details_json='{"api_token":"hidden"}', occurrence_count=1,
                first_seen=now, last_seen=now,
            ),
            ParserTask(
                id="task-1", user_id=2, status=ExportStatus.failed,
                source="yandex", params_json='{"target_count": 10, "token": "hidden"}',
                error="[PARSE-500] provider failure token=hidden", created_at=now,
                updated_at=now - timedelta(minutes=1),
            ),
            ParserTask(
                id="task-2", user_id=2, status=ExportStatus.queued,
                source="yandex", params_json='{"target_count": 10}', created_at=now,
                updated_at=now,
            ),
            ParserTask(
                id="task-stalled", user_id=2, status=ExportStatus.running,
                source="yandex", params_json='{"target_count": 10}', created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(minutes=11),
            ),
        ])
        db.commit()

    app = FastAPI()

    @app.middleware("http")
    async def add_request_id(request, call_next):
        request.state.request_id = "control-request"
        return await call_next(request)

    app.include_router(admin_control.router)

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    if role == "admin":
        def override_admin():
            with session_factory() as db:
                return db.get(User, 1)
        app.dependency_overrides[get_admin_user] = override_admin
    elif role == "user":
        def override_current():
            with session_factory() as db:
                return db.get(User, 2)
        app.dependency_overrides[get_current_user] = override_current
    return TestClient(app, raise_server_exceptions=False), engine


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/control/overview",
        "/api/admin/control/incidents",
        "/api/admin/control/tasks",
        "/api/admin/control/users/2",
        "/api/admin/control/users/2/audit",
    ],
)
def test_control_endpoints_require_authenticated_administrator(path):
    anonymous, anonymous_engine = _client("anonymous")
    regular, regular_engine = _client("user")

    assert anonymous.get(path).status_code == 401
    assert regular.get(path).status_code == 403

    anonymous_engine.dispose()
    regular_engine.dispose()


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/control/incidents",
        "/api/admin/control/tasks",
        "/api/admin/control/users/2/audit",
    ],
)
def test_control_lists_are_paginated_and_redacted(path):
    client, engine = _client()

    response = client.get(path, params={"page": 1, "page_size": 20})

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"items", "total", "page", "page_size"}
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert not {"password_hash", "download_token", "payment_method_id", "secret", "cookie"} & set(str(data).lower().split())
    assert "hidden" not in str(data)
    engine.dispose()


def test_control_rejects_page_size_over_one_hundred():
    client, engine = _client()

    response = client.get("/api/admin/control/incidents", params={"page_size": 101})

    assert response.status_code == 400
    engine.dispose()


def test_overview_returns_local_operational_snapshot_without_external_calls():
    client, engine = _client()

    response = client.get("/api/admin/control/overview")

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "generated_at", "users", "subscriptions", "tasks", "payments",
        "support", "incidents", "attention", "integrations",
    }
    assert data["users"]["active_24h"] == 1
    assert data["support"]["needs_human"] == 1
    assert data["incidents"]["critical"] == 1
    assert data["subscriptions"]["free"] == 2
    assert "hidden" not in str(data)
    engine.dispose()


def test_incidents_support_date_window_and_reject_invalid_dates():
    client, engine = _client()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        now = utcnow()
        db.add(AdminIncident(
            fingerprint="old", code="OLD-500", source="parser", severity="warning",
            status="new", message="old", occurrence_count=1,
            first_seen=now - timedelta(days=10), last_seen=now - timedelta(days=10),
        ))
        db.commit()

    response = client.get(
        "/api/admin/control/incidents",
        params={"date_from": (utcnow() - timedelta(days=1)).isoformat()},
    )
    assert response.status_code == 200
    assert [item["code"] for item in response.json()["items"]] == ["PARSE-500"]
    assert client.get(
        "/api/admin/control/incidents", params={"date_from": "not-a-date"}
    ).status_code == 400
    engine.dispose()


def test_incidents_date_to_includes_the_entire_calendar_day():
    client, engine = _client()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        db.add_all([
            AdminIncident(
                fingerprint="same-day", code="SAME-DAY", source="parser", severity="warning",
                status="new", message="same day", occurrence_count=1,
                first_seen=day + timedelta(hours=22), last_seen=day + timedelta(hours=22),
            ),
            AdminIncident(
                fingerprint="next-day", code="NEXT-DAY", source="parser", severity="warning",
                status="new", message="next day", occurrence_count=1,
                first_seen=day + timedelta(days=1), last_seen=day + timedelta(days=1),
            ),
        ])
        db.commit()

    response = client.get("/api/admin/control/incidents", params={"date_to": day.date().isoformat()})

    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["items"]}
    assert "SAME-DAY" in codes
    assert "NEXT-DAY" not in codes
    engine.dispose()


def test_incident_detail_endpoint_ignores_list_filters_and_uses_allowlist():
    client, engine = _client()

    response = client.get("/api/admin/control/incidents/1")

    assert response.status_code == 200
    assert response.json()["id"] == 1
    assert response.json()["status"] == "new"
    assert "hidden" not in str(response.json())
    engine.dispose()


def test_tasks_support_stalled_filter_for_attention_deep_links():
    client, engine = _client()

    response = client.get("/api/admin/control/tasks", params={"status": "running", "stalled": "true"})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["task-stalled"]
    assert response.json()["items"][0]["stalled"] is True
    engine.dispose()


def test_user_360_uses_allowlists_and_separate_limits():
    client, engine = _client()

    response = client.get("/api/admin/control/users/2")

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "profile", "subscription", "quotas", "recent_activity", "payments",
        "support_conversations", "audit_entries", "limits",
    }
    assert data["profile"]["email"] == "user@example.com"
    assert data["limits"] == {
        "recent_activity": 20, "payments": 20, "support_conversations": 20, "audit_entries": 20,
    }
    assert "password_hash" not in str(data)
    assert "payment_method_id" not in str(data)
    assert "hidden" not in str(data)
    engine.dispose()


def test_incident_state_change_requires_comment_and_writes_request_id_audit():
    client, engine = _client()

    rejected = client.patch("/api/admin/control/incidents/1", json={"status": "acknowledged", "comment": " "})
    accepted = client.patch(
        "/api/admin/control/incidents/1",
        json={"status": "acknowledged", "comment": "Проверяю источник"},
    )

    assert rejected.status_code == 400
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "acknowledged"
    with sessionmaker(bind=engine)() as db:
        audit = db.scalar(select(AdminAuditLog).where(AdminAuditLog.target_type == "incident"))
        assert audit is not None
        assert audit.request_id == "control-request"
    audit = client.get("/api/admin/control/users/2/audit").json()
    # This incident is not tied to a user, so verify the audit via the incident list's state only.
    assert audit["total"] == 0
    engine.dispose()


def test_incident_change_rolls_back_when_its_audit_cannot_be_written(monkeypatch):
    client, engine = _client()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(admin_control, "write_audit", fail_audit)
    response = client.patch(
        "/api/admin/control/incidents/1",
        json={"status": "acknowledged", "comment": "Проверяю источник"},
    )

    assert response.status_code == 500
    assert client.get("/api/admin/control/incidents").json()["items"][0]["status"] == "new"
    engine.dispose()


def test_resolved_incident_cannot_be_reopened_and_returns_conflict():
    client, engine = _client()
    assert client.patch(
        "/api/admin/control/incidents/1",
        json={"status": "resolved", "comment": "Источник восстановлен"},
    ).status_code == 200

    response = client.patch(
        "/api/admin/control/incidents/1",
        json={"status": "acknowledged", "comment": "Переоткрываю"},
    )

    assert response.status_code == 409
    engine.dispose()


def test_task_action_propagates_request_id_to_its_audit_row():
    client, engine = _client()

    response = client.post(
        "/api/admin/control/tasks/parser/task-2/cancel",
        json={"comment": "Останавливаю по запросу клиента"},
    )

    assert response.status_code == 200
    with sessionmaker(bind=engine)() as db:
        audit = db.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "task.cancel.request"))
        assert audit is not None
        assert audit.request_id == "control-request"
    engine.dispose()
