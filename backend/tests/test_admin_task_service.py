from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AdminAuditLog,
    AdminIncident,
    CompanyAnalysisTask,
    ExportStatus,
    ExportTask,
    ParserTask,
    ReviewTask,
    SocialAnalysisTask,
    User,
)
from app.services import admin_task_service
from app.timeutils import utcnow


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add_all([
            User(id=1, email="admin@example.com", password_hash="x", is_admin=True),
            User(id=2, email="customer@example.com", password_hash="x"),
        ])
        session.commit()
        yield session
    engine.dispose()


def _seed_all_task_types(db):
    now = utcnow()
    tasks = [
        ExportTask(
            id="export", user_id=2, status=ExportStatus.failed, progress=20,
            error="[EXP-501] export failed", requested_rows=40, filters_json="{}",
            created_at=now - timedelta(minutes=5),
        ),
        ParserTask(
            id="parser", user_id=2, status=ExportStatus.running, progress=10,
            kind="live_search", source="yandex",
            params_json='{"target_count": 30}', org_count=4,
            created_at=now, updated_at=now - timedelta(minutes=20),
        ),
        ReviewTask(
            id="review", user_id=2, status=ExportStatus.done, progress=100,
            org_ids_json="[1, 2]", created_at=now - timedelta(minutes=1), updated_at=now,
        ),
        CompanyAnalysisTask(
            id="company", user_id=2, saved_result_id=1, status=ExportStatus.queued,
            business_ids_json="[1, 2, 3]", options_json="{}", org_count=0,
            created_at=now - timedelta(minutes=2), updated_at=now,
        ),
        SocialAnalysisTask(
            id="social", user_id=2, saved_result_id=1, status=ExportStatus.partial,
            business_ids_json="[1]", org_count=1, error="partial result",
            created_at=now - timedelta(minutes=3), updated_at=now,
        ),
    ]
    db.add_all(tasks)
    db.commit()
    return tasks


def test_list_tasks_unifies_all_types_orders_and_marks_stalled(db):
    _seed_all_task_types(db)

    page = admin_task_service.list_tasks(db, page=1, page_size=20)

    assert page.total == 5
    assert [row.id for row in page.items] == ["parser", "review", "company", "social", "export"]
    assert {row.type for row in page.items} == {"export", "parser", "review", "company", "social"}
    parser = next(row for row in page.items if row.type == "parser")
    assert parser.user_email == "customer@example.com"
    assert parser.requested_count == 30
    assert parser.found_count == 4
    assert parser.stalled is True
    export = next(row for row in page.items if row.type == "export")
    assert export.error_code == "EXP-501"
    assert export.error_message == "[EXP-501] export failed"


def test_list_tasks_rejects_invalid_page_size(db):
    with pytest.raises(ValueError, match="page_size"):
        admin_task_service.list_tasks(db, page=1, page_size=101)


def test_task_detail_includes_last_related_incident_request_id(db):
    task = ParserTask(
        id="request-id-task", user_id=2, status=ExportStatus.failed,
        kind="live_search", source="yandex", params_json="{}", error="[MAP-500] failed",
    )
    db.add_all([
        task,
        AdminIncident(
            fingerprint="parser:MAP-500", code="MAP-500", source="parser",
            severity="critical", status="new", message="failed", occurrence_count=1,
            first_seen=utcnow(), last_seen=utcnow(), task_type="parser",
            task_id="request-id-task", request_id="req-task-123",
        ),
    ])
    db.commit()

    assert admin_task_service.get_task(db, "parser", task.id).request_id == "req-task-123"


def test_request_cancel_queues_terminal_status_and_audit(db):
    task = ParserTask(
        id="cancel-me", user_id=2, status=ExportStatus.queued, kind="live_search",
        source="yandex", params_json="{}",
    )
    db.add(task)
    db.commit()

    detail = admin_task_service.request_cancel(
        db, "parser", task.id, admin_id=1, comment="operator request token=secret-value"
    )

    assert detail.status == "canceled"
    assert detail.cancel_requested is True
    assert db.get(ParserTask, task.id).finished_at is not None
    audit = db.query(AdminAuditLog).one()
    assert audit.action == "task.cancel.request"
    assert "secret-value" not in audit.comment


def test_request_cancel_running_sets_cooperative_flag(db):
    task = ReviewTask(
        id="running", user_id=2, status=ExportStatus.running, org_ids_json="[1]"
    )
    db.add(task)
    db.commit()

    detail = admin_task_service.request_cancel(
        db, "review", task.id, admin_id=1, comment="requested by owner"
    )

    assert detail.status == "running"
    assert detail.cancel_requested is True


def test_retry_failed_task_copies_only_inputs_and_audits(db):
    original = ParserTask(
        id="failed-parser", user_id=2, status=ExportStatus.failed, progress=88,
        error="[MAP-503] source failed", kind="live_search", source="yandex",
        params_json='{"target_count": 20, "token": "must-not-be-logged"}',
        result_json='{"old": true}', progress_json='{"found": 9}', org_count=9,
        credits_charged=9, download_id=7, download_token="download-secret",
    )
    db.add(original)
    db.commit()

    retried = admin_task_service.retry_task(
        db, "parser", original.id, admin_id=1, comment="retry after provider recovery"
    )

    assert retried.id != original.id
    clone = db.get(ParserTask, retried.id)
    assert clone.retry_of_id == original.id
    assert clone.status == ExportStatus.queued
    assert clone.progress == 0
    assert clone.error is None
    assert clone.result_json is None
    assert clone.progress_json is None
    assert clone.org_count == 0
    assert clone.credits_charged == 0
    assert clone.download_id is None
    assert clone.download_token is None
    assert clone.kind == original.kind
    assert clone.source == original.source
    assert clone.params_json == original.params_json
    audit = db.query(AdminAuditLog).one()
    assert audit.action == "task.retry"
    assert "must-not-be-logged" not in (audit.before_json or "")
    assert "download-secret" not in (audit.before_json or "")


def test_retry_rejects_payment_type_and_success_without_error(db):
    task = ExportTask(
        id="done", user_id=2, status=ExportStatus.done, progress=100,
        requested_rows=1, filters_json="{}",
    )
    db.add(task)
    db.commit()

    with pytest.raises(ValueError, match="тип"):
        admin_task_service.retry_task(db, "payment", "x", 1, "not allowed")
    with pytest.raises(ValueError, match="Успеш"):
        admin_task_service.retry_task(db, "export", task.id, 1, "not needed")
