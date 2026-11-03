import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AdminAuditLog, AdminCreditGrant, User
from app.services.admin_actions import grant_credits, set_block
from app.services.admin_audit_service import write_audit


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        yield session
    engine.dispose()


def test_write_audit_redacts_all_caller_controlled_fields_without_committing(db):
    audit = write_audit(
        db,
        admin_id=1,
        action="user.plan.assign?token=action-secret",
        target_type="user?cookie=type-secret",
        target_id="2?password=target-secret",
        before={"authorization": "Bearer before-secret", "plan_code": "pro"},
        after={"plan_code": "business", "nested": {"token": "after-secret"}},
        comment="Authorization: Bearer comment-secret",
        request_id="request?secret=request-secret",
    )

    assert audit.id is None
    assert audit.action == "user.plan.assign?token=***"
    assert audit.target_type == "user?cookie=***"
    assert audit.target_id == "2?password=***"
    assert json.loads(audit.before_json) == {"authorization": "***", "plan_code": "pro"}
    assert json.loads(audit.after_json) == {"plan_code": "business", "nested": {"token": "***"}}
    assert audit.comment == "Authorization: ***"
    assert audit.request_id == "request?secret=***"
    assert db.query(AdminAuditLog).count() == 1


@pytest.mark.parametrize("comment", ["", "   "])
def test_write_audit_requires_a_nonempty_comment(db, comment):
    with pytest.raises(ValueError, match="Комментарий не может быть пустым"):
        write_audit(
            db,
            admin_id=1,
            action="user.plan.assign",
            target_type="user",
            target_id=2,
            comment=comment,
        )


def test_admin_actions_still_commit_by_default(db):
    db.add(User(id=2, email="customer@example.com", password_hash="x", credits_balance=10))
    db.commit()

    assert grant_credits(db, admin_id=1, user_id=2, delta=5, comment="Manual adjustment") == 15
    assert set_block(db, user_id=2, blocked=True, comment="Policy violation") is True
    db.rollback()

    user = db.get(User, 2)
    assert user.credits_balance == 15
    assert user.is_blocked is True


def test_admin_actions_require_nonempty_comments(db):
    db.add(User(id=2, email="customer@example.com", password_hash="x", credits_balance=10))
    db.commit()

    with pytest.raises(ValueError, match="Комментарий не может быть пустым"):
        grant_credits(db, admin_id=1, user_id=2, delta=5, comment="   ")
    with pytest.raises(ValueError, match="Комментарий не может быть пустым"):
        set_block(db, user_id=2, blocked=True, comment="")


def test_grant_credits_redacts_raw_admin_credit_grant_comment(db):
    db.add(User(id=2, email="customer@example.com", password_hash="x", credits_balance=10))
    db.commit()

    grant_credits(
        db,
        admin_id=1,
        user_id=2,
        delta=5,
        comment="Support review token=grant-secret",
    )

    grant = db.query(AdminCreditGrant).one()
    assert grant.comment == "Support review token=***"
