import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.services.admin_incident_service import (
    record_incident,
    safe_details,
    update_incident,
)


T0 = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        yield session
    engine.dispose()


def test_same_failure_is_grouped_within_fifteen_minutes(db):
    first = record_incident(
        db,
        code="MAP-500",
        source="yandex",
        message="Timeout after 30 seconds",
        now=T0,
    )
    second = record_incident(
        db,
        code="MAP-500",
        source="yandex",
        message="Timeout after 60 seconds",
        now=T0 + timedelta(minutes=5),
    )

    assert second.id == first.id
    assert second.occurrence_count == 2
    assert second.last_seen == T0 + timedelta(minutes=5)


def test_resolved_incident_is_not_reopened(db):
    old = record_incident(
        db, code="MAP-500", source="yandex", message="timeout", now=T0
    )
    update_incident(db, old.id, status="resolved", admin_id=1, comment="Источник восстановлен")

    new = record_incident(
        db,
        code="MAP-500",
        source="yandex",
        message="timeout",
        now=T0 + timedelta(minutes=1),
    )

    assert new.id != old.id


def test_normalizes_dynamic_values_before_grouping(db):
    first = record_incident(
        db,
        code="MAP-500",
        source="yandex",
        message="Request 123456789012345678 to https://maps.example/path?token=one failed after 30 seconds, trace 550e8400-e29b-41d4-a716-446655440000",
        now=T0,
    )
    second = record_incident(
        db,
        code="MAP-500",
        source="yandex",
        message="Request 987654321098765432 to https://maps.example/path?token=two failed after 2 minutes, trace 9f8c7b6a-5d4e-3c2b-1a09-876543210fed",
        now=T0 + timedelta(minutes=1),
    )

    assert second.id == first.id


def test_safe_details_masks_sensitive_keys_at_every_nesting_level():
    details = {
        "api_token": "abc",
        "nested": {"authorization": "Bearer top-secret"},
        "items": [{"payment_method": "card"}, {"cookie_value": "session"}],
        "public": "visible",
    }

    assert safe_details(details) == {
        "api_token": "***",
        "nested": {"authorization": "***"},
        "items": [{"payment_method": "***"}, {"cookie_value": "***"}],
        "public": "visible",
    }
    assert details["api_token"] == "abc"


def test_record_incident_serializes_only_redacted_details(db):
    incident = record_incident(
        db,
        code="MAP-401",
        source="yandex",
        message="Unauthorized",
        details={"outer": [{"secret": "do-not-store"}], "count": 3},
        now=T0,
    )

    assert json.loads(incident.details_json) == {
        "outer": [{"secret": "***"}],
        "count": 3,
    }


def test_safe_details_requires_a_dictionary():
    with pytest.raises(TypeError, match="details must be a dictionary"):
        safe_details(["not", "a", "dictionary"])


def test_record_incident_redacts_caller_controlled_scalars_and_details(db):
    incident = record_incident(
        db,
        code="MAP-500?token=code-secret",
        source="yandex?password=source-secret",
        message=(
            "Request failed at https://maps.example/path?token=url-secret&region=ru; "
            "Authorization: Bearer auth-secret; password=database-secret; "
            "payment_method=card-secret"
        ),
        request_id="request_id?secret=request-secret",
        task_type="import?token=type-secret",
        task_id="task_id?cookie=cookie-secret",
        details={"context": "token=details-secret", "safe": "visible"},
        now=T0,
    )

    persisted = "\n".join(
        [
            incident.code,
            incident.source,
            incident.message,
            incident.request_id or "",
            incident.task_type or "",
            incident.task_id or "",
            incident.details_json or "",
        ]
    )
    for secret in (
        "code-secret",
        "source-secret",
        "url-secret",
        "auth-secret",
        "database-secret",
        "card-secret",
        "request-secret",
        "type-secret",
        "cookie-secret",
        "details-secret",
    ):
        assert secret not in persisted
    assert "region=ru" in incident.message
    assert json.loads(incident.details_json) == {"context": "token=***", "safe": "visible"}


def test_fingerprint_uses_sanitized_normalized_message(db):
    first = record_incident(
        db,
        code="MAP-401",
        source="yandex",
        message="Unauthorized: Authorization: Bearer first-secret",
        now=T0,
    )
    second = record_incident(
        db,
        code="MAP-401",
        source="yandex",
        message="Unauthorized: Authorization: Bearer second-secret",
        now=T0 + timedelta(minutes=1),
    )

    assert second.id == first.id
    assert first.fingerprint == second.fingerprint
    assert "first-secret" not in second.message


@pytest.mark.parametrize("status", ["closed", "", "NEW"])
def test_update_incident_rejects_invalid_status(db, status):
    incident = record_incident(
        db, code="MAP-500", source="yandex", message="timeout", now=T0
    )

    with pytest.raises(ValueError, match="Invalid incident status"):
        update_incident(db, incident.id, status=status, admin_id=1, comment="nope")


def test_update_incident_requires_existing_incident(db):
    with pytest.raises(ValueError, match="Incident not found"):
        update_incident(db, 999, status="resolved", admin_id=1, comment="nope")


@pytest.mark.parametrize("status", ["new", "acknowledged"])
def test_resolved_incident_cannot_transition_to_non_resolved_status(db, status):
    incident = record_incident(
        db, code="MAP-500", source="yandex", message="timeout", now=T0
    )
    update_incident(db, incident.id, status="resolved", admin_id=1, comment="fixed")

    with pytest.raises(ValueError, match="Resolved incident cannot be reopened"):
        update_incident(db, incident.id, status=status, admin_id=2, comment="reopen")


def test_resolving_an_incident_is_idempotent_and_redacts_comment(db):
    incident = record_incident(
        db, code="MAP-500", source="yandex", message="timeout", now=T0
    )
    update_incident(
        db,
        incident.id,
        status="resolved",
        admin_id=1,
        comment="Authorization: Bearer initial-secret",
    )

    result = update_incident(
        db,
        incident.id,
        status="resolved",
        admin_id=2,
        comment="token=repeat-secret",
    )

    assert result.status == "resolved"
    assert result.comment == "token=***"
    assert result.resolved_by == 2
