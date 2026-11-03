import json

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import (
    AdminIncident,
    CompanyAnalysisTask,
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


def test_equivalent_parser_worker_failures_create_one_redacted_incident(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'incidents.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id=1, email="customer@example.com", password_hash="x"))
        db.add_all([
            ParserTask(id="broken-1", user_id=1, kind="live_search", source="yandex", params_json="{}"),
            ParserTask(id="broken-2", user_id=1, kind="live_search", source="yandex", params_json="{}"),
        ])
        db.commit()

    async def fail_worker(_db, _task, _user):
        raise RuntimeError("provider token=super-secret timed out")

    monkeypatch.setattr(maps_service, "SessionLocal", factory)
    monkeypatch.setattr(maps_service, "_run_live_search", fail_worker)

    maps_service.run_parser_task("broken-1")
    maps_service.run_parser_task("broken-2")

    with factory() as db:
        incident = db.query(AdminIncident).one()
        assert incident.code == "PARSER-500"
        assert incident.occurrence_count == 2
        assert "super-secret" not in incident.message
        assert "super-secret" not in (incident.details_json or "")
        assert db.get(ParserTask, "broken-1").status == ExportStatus.failed
        assert db.get(ParserTask, "broken-2").status == ExportStatus.failed
    engine.dispose()


class _FailFirstCommitSession(Session):
    """Fails only the worker's first transition commit, then permits recovery."""

    def commit(self):
        if self.info.pop("fail_first_commit", False):
            raise RuntimeError("provider token=initial-secret failed")
        return super().commit()


@pytest.mark.parametrize(
    ("module", "runner", "model", "task", "code"),
    [
        (
            export_service,
            export_service.run_export,
            ExportTask,
            ExportTask(id="early-export", user_id=1, requested_rows=1, filters_json="{}"),
            "EXPORT-500",
        ),
        (
            maps_service,
            maps_service.run_parser_task,
            ParserTask,
            ParserTask(
                id="early-parser", user_id=1, kind="live_search", source="yandex",
                params_json=json.dumps({"query": "test", "location": "Moscow"}),
            ),
            "PARSER-500",
        ),
        (
            reviews_service,
            reviews_service.run_review_task,
            ReviewTask,
            ReviewTask(id="early-review", user_id=1, org_ids_json="[1]"),
            "REVIEWS-500",
        ),
        (
            company_analysis_service,
            company_analysis_service.run_company_analysis_task,
            CompanyAnalysisTask,
            CompanyAnalysisTask(
                id="early-company", user_id=1, saved_result_id=1,
                business_ids_json="[1]", options_json="{}",
            ),
            "ANALYSIS-500",
        ),
        (
            social_analysis_service,
            social_analysis_service.run_social_task,
            SocialAnalysisTask,
            SocialAnalysisTask(
                id="early-social", user_id=1, saved_result_id=1, business_ids_json="[1]",
            ),
            "SOCIAL-500",
        ),
    ],
)
def test_initial_transition_failure_is_terminal_and_incident_recorded(
    monkeypatch, tmp_path, module, runner, model, task, code
):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / (task.id + '.db')}")
    Base.metadata.create_all(engine)
    normal_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with normal_factory() as db:
        db.add(User(id=1, email="customer@example.com", password_hash="x"))
        db.add(task)
        db.commit()

    def worker_session():
        db = _FailFirstCommitSession(bind=engine, expire_on_commit=False)
        db.info["fail_first_commit"] = True
        return db

    monkeypatch.setattr(module, "SessionLocal", worker_session)
    runner(task.id)

    with normal_factory() as db:
        stored = db.get(model, task.id)
        incident = db.query(AdminIncident).one()
        assert stored.status == ExportStatus.failed
        assert stored.finished_at is not None
        assert incident.code == code
        assert "initial-secret" not in (stored.error or "")
        assert "initial-secret" not in incident.message
        assert "initial-secret" not in (incident.details_json or "")
    engine.dispose()
