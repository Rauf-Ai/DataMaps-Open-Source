from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_admin_user, get_current_user
from app.models import AdminAuditLog, ExportStatus, ParserTask, User
from app.routers import admin_control
from app.services.admin_incident_service import record_incident
from app.timeutils import utcnow


def _environment(role="admin"):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([
            User(id=1, email="admin@datamaps.ru", password_hash="x", is_admin=True),
            User(id=2, email="client@example.com", password_hash="x", credits_balance=50),
        ])
        task = ParserTask(
            id="failed-live-search", user_id=2, status=ExportStatus.failed,
            kind="live_search", source="yandex", params_json='{"target_count": 20}',
            error="[PARSER-500] provider failed", credits_charged=0,
            created_at=utcnow(), updated_at=utcnow(), finished_at=utcnow(),
        )
        db.add(task)
        db.flush()
        record_incident(
            db, code="PARSER-500", source="parser", severity="critical",
            message="provider failed", task_type="parser", task_id=task.id,
            user_id=2, request_id="e2e-request",
            details={"exception_class": "RuntimeError"},
        )
        db.commit()

    app = FastAPI()

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "e2e-admin-request"
        return await call_next(request)

    app.include_router(admin_control.router)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    if role == "admin":
        def override_admin():
            with factory() as db:
                return db.get(User, 1)
        app.dependency_overrides[get_admin_user] = override_admin
    elif role == "user":
        def override_user():
            with factory() as db:
                return db.get(User, 2)
        app.dependency_overrides[get_current_user] = override_user
    return TestClient(app, raise_server_exceptions=False), factory, engine


def test_failure_to_incident_user_retry_cancel_and_audit_flow():
    client, factory, engine = _environment()

    incidents = client.get("/api/admin/control/incidents", params={"q": "PARSER-500"})
    assert incidents.status_code == 200
    assert incidents.json()["items"][0]["request_id"] == "e2e-request"

    user = client.get("/api/admin/control/users/2")
    assert user.status_code == 200
    assert user.json()["profile"]["credits_balance"] == 50

    retry = client.post(
        "/api/admin/control/tasks/parser/failed-live-search/retry",
        json={"comment": "Повтор после восстановления провайдера"},
    )
    assert retry.status_code == 200
    retried_id = retry.json()["id"]
    assert retry.json()["retry_of_id"] == "failed-live-search"
    assert retry.json()["status"] == "queued"

    cancel = client.post(
        f"/api/admin/control/tasks/parser/{retried_id}/cancel",
        json={"comment": "Проверка безопасной отмены"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "canceled"

    with factory() as db:
        original = db.get(ParserTask, "failed-live-search")
        retried = db.get(ParserTask, retried_id)
        customer = db.get(User, 2)
        actions = db.scalars(select(AdminAuditLog.action).order_by(AdminAuditLog.id)).all()
        assert original.credits_charged == 0
        assert retried.credits_charged == 0
        assert customer.credits_balance == 50
        assert actions == ["task.retry", "task.cancel.request"]
        assert all(row.request_id == "e2e-admin-request" for row in db.scalars(select(AdminAuditLog)).all())
    engine.dispose()


def test_regular_user_cannot_access_control_center_endpoints():
    client, _, engine = _environment("user")
    paths = [
        "/api/admin/control/overview",
        "/api/admin/control/incidents",
        "/api/admin/control/tasks",
        "/api/admin/control/users/2",
    ]
    assert [client.get(path).status_code for path in paths] == [403, 403, 403, 403]
    engine.dispose()
