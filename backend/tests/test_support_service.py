import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AdminAuditLog,
    AdminCreditGrant,
    PendingAction,
    SupportConversation,
    User,
)
from app.services.support_kb_service import KnowledgeChunk
from app.services import support_service


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("kind", ["grant_credits", "block_user", "unblock_user"])
def test_pending_action_reason_is_sanitized_and_execution_writes_audit_trail(db, kind):
    db.add_all([
        User(id=1, email="admin@example.com", password_hash="x", is_admin=True),
        User(id=2, email="customer@example.com", password_hash="x", credits_balance=20),
    ])
    db.flush()

    created = support_service._create_pending_action(
        db,
        conv_id=7,
        user=db.get(User, 2),
        action={"kind": kind, "delta": 5, "reason": "token=support-secret"},
    )
    assert created is not None
    pending_action_id, _notification = created
    db.commit()
    pending_action = db.get(PendingAction, pending_action_id)
    assert pending_action is not None
    expected_payload = {"reason": "token=***"}
    if kind == "grant_credits":
        expected_payload["delta"] = 5
    assert json.loads(pending_action.payload_json) == expected_payload
    assert "support-secret" not in pending_action.payload_json

    ok, _result = support_service.execute_pending_action(db, pending_action, approver_admin_id=1)

    assert ok is True
    db.refresh(pending_action)
    assert pending_action.status == "executed"
    assert "support-secret" not in (pending_action.result or "")

    audit = db.query(AdminAuditLog).one()
    assert audit.admin_id == 1
    assert audit.target_type == "user"
    assert audit.target_id == "2"
    assert audit.comment == f"support pending action {pending_action_id}: token=***"
    assert "support-secret" not in audit.comment
    assert "support-secret" not in (audit.before_json or "")
    assert "support-secret" not in (audit.after_json or "")

    user = db.get(User, 2)
    if kind == "grant_credits":
        assert user.credits_balance == 25
        assert audit.action == "user.credits.adjust"
        grant = db.query(AdminCreditGrant).one()
        assert grant.comment == audit.comment
        assert "support-secret" not in (grant.comment or "")
    else:
        assert user.is_blocked is (kind == "block_user")
        assert audit.action == "user.block.set"


def test_build_system_prompt_includes_retrieved_chunks_and_live_pricing(monkeypatch, db):
    monkeypatch.setattr(support_service.support_kb_service, "refresh_if_due", lambda *_args: None)
    monkeypatch.setattr(
        support_service.support_kb_service,
        "retrieve",
        lambda *_args, **_kwargs: [
            KnowledgeChunk("billing", "Оплата", "Автопродление", "Отключите в Кабинете."),
        ],
    )

    prompt = support_service._build_system_prompt(
        db, user=None, user_query="отменить подписку",
    )

    assert "=== РЕЛЕВАНТНЫЕ ЗНАНИЯ ===" in prompt
    assert "Отключите в Кабинете." in prompt
    assert "=== АКТУАЛЬНЫЕ ПАКЕТЫ ===" in prompt


def test_empty_retrieval_escalates_without_calling_llm(monkeypatch, db):
    conversation = SupportConversation(guest_id="guest", status="open")
    db.add(conversation)
    db.commit()
    monkeypatch.setattr(support_service.support_kb_service, "refresh_if_due", lambda *_args: None)
    monkeypatch.setattr(support_service.support_kb_service, "retrieve", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(support_service, "chat_json", pytest.fail)

    async def no_notify(*_args, **_kwargs):
        return None

    monkeypatch.setattr(support_service, "_notify_human", no_notify)
    result = asyncio.run(
        support_service.handle_message(db, conversation, None, "неизвестный вопрос"),
    )

    assert result["needs_human"] is True
    assert "оператор" in result["reply"].lower()
