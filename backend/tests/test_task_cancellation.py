import json
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    CompanyAnalysisTask,
    AdminIncident,
    ExportStatus,
    ExportTask,
    ParserTask,
    ReviewTask,
    SocialAnalysisTask,
    User,
)
from app.services import (
    company_analysis_service,
    export_service,
    maps_service,
    reviews_service,
    social_analysis_service,
)
from app.services.admin_task_service import finalize_task_unless_cancelled, stop_if_cancelled


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tasks.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id=1, email="customer@example.com", password_hash="x", credits_balance=50))
        db.add_all([
            ExportTask(id="export-cancel", user_id=1, cancel_requested=True, requested_rows=1, filters_json="{}"),
            ParserTask(
                id="parser-cancel", user_id=1, cancel_requested=True,
                kind="live_search", source="yandex", params_json=json.dumps({"query": "secret query"}),
            ),
            ReviewTask(id="review-cancel", user_id=1, cancel_requested=True, org_ids_json="[1]"),
            CompanyAnalysisTask(
                id="company-cancel", user_id=1, saved_result_id=1, cancel_requested=True,
                business_ids_json="[1]", options_json='{"photos": true}',
            ),
            SocialAnalysisTask(
                id="social-cancel", user_id=1, saved_result_id=1, cancel_requested=True,
                business_ids_json="[1]",
            ),
        ])
        db.commit()
    yield factory
    engine.dispose()


def test_cancel_requested_before_worker_starts_never_charges_quota(monkeypatch, session_factory):
    for module in (
        export_service,
        maps_service,
        reviews_service,
        company_analysis_service,
        social_analysis_service,
    ):
        monkeypatch.setattr(module, "SessionLocal", session_factory)

    export_service.run_export("export-cancel")
    maps_service.run_parser_task("parser-cancel")
    reviews_service.run_review_task("review-cancel")
    company_analysis_service.run_company_analysis_task("company-cancel")
    social_analysis_service.run_social_task("social-cancel")

    with session_factory() as db:
        tasks = [
            db.get(ExportTask, "export-cancel"),
            db.get(ParserTask, "parser-cancel"),
            db.get(ReviewTask, "review-cancel"),
            db.get(CompanyAnalysisTask, "company-cancel"),
            db.get(SocialAnalysisTask, "social-cancel"),
        ]
        assert all(task.status == ExportStatus.canceled for task in tasks)
        assert all(task.finished_at is not None for task in tasks)
        assert db.get(User, 1).credits_balance == 50
        assert all(getattr(task, "credits_charged", getattr(task, "quota_charged", 0)) == 0 for task in tasks)
        assert db.query(AdminIncident).count() == 0


@pytest.mark.parametrize(
    ("model", "task_id"),
    [
        (ExportTask, "export-cancel"),
        (ParserTask, "parser-cancel"),
        (ReviewTask, "review-cancel"),
        (CompanyAnalysisTask, "company-cancel"),
        (SocialAnalysisTask, "social-cancel"),
    ],
)
def test_cancel_between_external_work_and_finalization_wins_without_charge(
    session_factory, model, task_id
):
    with session_factory() as setup:
        task = setup.get(model, task_id)
        task.status = ExportStatus.running
        task.cancel_requested = False
        setup.commit()

    with session_factory() as worker_db:
        task = worker_db.get(model, task_id)
        user = worker_db.get(User, 1)
        user.credits_balance -= 1

        # Имитирует отмену из admin API после внешнего вызова, но до
        # атомарного commit списания и terminal status воркера.
        with session_factory() as cancel_db:
            cancel_task = cancel_db.get(model, task_id)
            cancel_task.cancel_requested = True
            cancel_db.commit()

        claimed = finalize_task_unless_cancelled(worker_db, task, ExportStatus.done)

    with session_factory() as check:
        task = check.get(model, task_id)
        assert claimed is False
        assert task.status == ExportStatus.canceled
        assert task.finished_at is not None
        assert check.get(User, 1).credits_balance == 50


def test_stop_if_cancelled_propagates_refresh_failures(session_factory, monkeypatch):
    with session_factory() as db:
        task = db.get(ExportTask, "export-cancel")

        def broken_refresh(_task):
            raise RuntimeError("database connection lost")

        monkeypatch.setattr(db, "refresh", broken_refresh)
        with pytest.raises(RuntimeError, match="database connection lost"):
            stop_if_cancelled(db, task)


def test_stop_if_cancelled_treats_concurrently_deleted_task_as_stopped(session_factory):
    with session_factory() as worker_db:
        task = worker_db.get(ExportTask, "export-cancel")
        with session_factory() as delete_db:
            delete_db.delete(delete_db.get(ExportTask, "export-cancel"))
            delete_db.commit()
        assert stop_if_cancelled(worker_db, task) is True


def test_parser_cancellation_during_external_collection_stops_before_artifact_and_charge(
    monkeypatch, session_factory
):
    monkeypatch.setattr(maps_service.settings, "yandex_search_api_key", "test-key")

    with session_factory() as setup:
        task = setup.get(ParserTask, "parser-cancel")
        task.cancel_requested = False
        task.status = ExportStatus.running
        task.params_json = json.dumps({
            "target_count": 1,
            "searches": [{"query": "dentist", "location": "Moscow", "source": "yandex"}],
        })
        setup.commit()

    def collect_and_cancel(*_args, **_kwargs):
        with session_factory() as cancel_db:
            current = cancel_db.get(ParserTask, "parser-cancel")
            current.cancel_requested = True
            cancel_db.commit()
        return [{"oid": "1", "name": "Clinic"}]

    monkeypatch.setattr(maps_service, "_collect_jobs", collect_and_cancel)
    with session_factory() as worker_db:
        task = worker_db.get(ParserTask, "parser-cancel")
        user = worker_db.get(User, 1)
        asyncio.run(maps_service._run_live_search(worker_db, task, user))

    with session_factory() as check:
        task = check.get(ParserTask, "parser-cancel")
        assert task.status == ExportStatus.canceled
        assert task.credits_charged == 0
        assert task.download_id is None
        assert check.get(User, 1).credits_balance == 50
        assert check.query(AdminIncident).count() == 0
