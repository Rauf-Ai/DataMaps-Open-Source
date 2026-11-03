import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AdminAuditLog, AdminIncident
from app.timeutils import utcnow


def _incident(**overrides):
    values = {
        "fingerprint": "maps:E9:timeout",
        "code": "E9",
        "source": "parser",
        "severity": "critical",
        "status": "new",
        "message": "Источник не ответил",
        "occurrence_count": 1,
        "first_seen": utcnow(),
        "last_seen": utcnow(),
    }
    values.update(overrides)
    return AdminIncident(**values)


def test_incident_and_audit_tables_are_created():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as db:
        db.add(
            AdminIncident(
                fingerprint="maps:E9:timeout",
                code="E9",
                source="parser",
                severity="critical",
                status="new",
                message="Источник не ответил",
                occurrence_count=1,
                first_seen=utcnow(),
                last_seen=utcnow(),
            )
        )
        db.add(
            AdminAuditLog(
                admin_id=1,
                action="user.plan.assign",
                target_type="user",
                target_id="2",
                comment="Компенсация",
                created_at=utcnow(),
            )
        )
        db.commit()

        assert db.scalar(select(func.count(AdminIncident.id))) == 1
        assert db.scalar(select(func.count(AdminAuditLog.id))) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("severity", "fatal"), ("status", "closed")],
)
def test_incident_rejects_invalid_enum_values(field, value):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as db:
        db.add(_incident(**{field: value}))
        with pytest.raises(IntegrityError):
            db.commit()
