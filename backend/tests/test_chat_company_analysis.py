import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import (
    Business,
    BusinessSource,
    ChatMessage,
    ChatSession,
    CompanyAnalysisTask,
    ExportStatus,
    ParserTask,
    SavedResult,
    SavedResultItem,
    User,
)
from app.routers import chat
from app.timeutils import utcnow


class ChatCompanyAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            db.add(User(id=1, email="owner@example.com", password_hash="x", credits_balance=100, email_verified_at=utcnow()))
            db.add(ChatSession(id="chat-1", user_id=1, title="Клиники", search_state_json="{}"))
            db.add(Business(id=10, name="Клиника А"))
            db.add(SavedResult(id=20, user_id=1, title="Клиники", params_json="{}", row_count=1))
            db.flush()
            db.add(SavedResultItem(saved_result_id=20, business_id=10, position=1, source="yandex"))
            db.add(BusinessSource(business_id=10, source="yandex", source_id="123", source_url="https://yandex.ru/maps/org/123/"))
            db.commit()

        app = FastAPI()
        app.include_router(chat.router)

        def override_db():
            with self.Session() as db:
                yield db

        def override_user():
            with self.Session() as db:
                return db.get(User, 1)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        self.client = TestClient(app)

    def tearDown(self):
        self.engine.dispose()

    def test_analysis_intent_appends_user_and_configuration_messages(self):
        response = self.client.post(
            "/api/chat/sessions/chat-1/analysis-intent",
            json={"saved_result_id": 20, "business_ids": [10]},
        )

        self.assertEqual(response.status_code, 200)
        messages = response.json()["messages"]
        self.assertEqual(messages[-2]["metadata"]["type"], "company_analysis_request")
        self.assertEqual(messages[-1]["metadata"]["type"], "company_analysis_config")
        self.assertEqual(messages[-1]["metadata"]["business_ids"], [10])

    def test_analysis_intent_exposes_only_eligible_channels_and_personal_notice(self):
        async def discovery(_target):
            return {
                "website": [{"business_id": 10, "key": "website", "label": "Сайт", "url": "https://clinic.test/", "status": "available", "reason": ""}],
                "telegram": [{"business_id": 10, "key": "telegram", "label": "Telegram", "url": "https://t.me/alice", "status": "personal", "reason": "Личный Telegram, не анализируется"}],
                "vk": [{"business_id": 10, "key": "vk", "label": "ВКонтакте", "url": "https://vk.com/clinic", "status": "available", "reason": ""}],
            }

        with patch("app.routers.chat.discover_company_channels", new=discovery):
            response = self.client.post(
                "/api/chat/sessions/chat-1/analysis-intent",
                json={"saved_result_id": 20, "business_ids": [10]},
            )

        self.assertEqual(response.status_code, 200)
        metadata = response.json()["messages"][-1]["metadata"]
        self.assertEqual(metadata["available_channels"], {"website": [10], "telegram": [], "vk": [10]})
        self.assertEqual(metadata["unavailable_channels"], [{
            "business_id": 10,
            "key": "telegram",
            "label": "Telegram",
            "url": "https://t.me/alice",
            "status": "personal",
            "reason": "Личный Telegram, не анализируется",
        }])

    def test_start_company_analysis_rejects_forged_unavailable_channel(self):
        async def discovery(_target):
            return {
                "website": [{"business_id": 10, "status": "missing"}],
                "telegram": [{"business_id": 10, "status": "personal"}],
                "vk": [{"business_id": 10, "status": "missing"}],
            }

        with patch("app.routers.chat.discover_company_channels", new=discovery):
            response = self.client.post(
                "/api/chat/sessions/chat-1/company-analysis",
                json={
                    "saved_result_id": 20,
                    "business_ids": [10],
                    "options": {"telegram": True},
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Telegram", response.json()["detail"])

    def test_search_completion_message_is_idempotent(self):
        with self.Session() as db:
            task = ParserTask(
                id="search-1",
                user_id=1,
                status=ExportStatus.done,
                progress=100,
                kind="live_search",
                source="yandex",
                params_json='{"target_count": 5}',
                result_json='{"saved_result_id": 20}',
                progress_json='{"target_count": 5, "checked_count": 37}',
                org_count=4,
            )
            db.add(task)
            db.get(ChatSession, "chat-1").parser_task_id = task.id
            db.commit()

        self.client.get("/api/chat/sessions/chat-1/task")
        self.client.get("/api/chat/sessions/chat-1/task")

        with self.Session() as db:
            messages = db.execute(select(ChatMessage).where(ChatMessage.session_id == "chat-1")).scalars().all()
            completion = [
                message for message in messages
                if json.loads(message.metadata_json or "{}").get("type") == "search_completion"
            ]
        self.assertEqual(len(completion), 1)
        self.assertIn("Найдено 4 из 5", completion[0].content)
        self.assertIn("глубокий анализ", completion[0].content.lower())
        metadata = json.loads(completion[0].metadata_json)
        self.assertEqual(metadata["saved_result_id"], 20)
        self.assertEqual(metadata["action"], "open_search_result")

    def test_start_company_analysis_creates_one_unified_task(self):
        with patch("app.routers.chat.ensure_task_slot"), \
                patch("app.routers.chat.quota_available", return_value=10), \
                patch("app.routers.chat.run_company_analysis_task") as runner:
            response = self.client.post(
                "/api/chat/sessions/chat-1/company-analysis",
                json={
                    "saved_result_id": 20,
                    "business_ids": [10],
                    "options": {"photos": True, "reviews": True, "review_count": 20},
                },
            )

        self.assertEqual(response.status_code, 200)
        task_id = response.json()["task_id"]
        with self.Session() as db:
            task = db.get(CompanyAnalysisTask, task_id)
            self.assertEqual(json.loads(task.business_ids_json), [10])
            self.assertEqual(json.loads(task.options_json)["review_count"], 20)
        runner.assert_called_once_with(task_id)

    def test_session_detail_exposes_latest_company_analysis_task(self):
        with self.Session() as db:
            db.add(CompanyAnalysisTask(
                id="analysis-latest",
                user_id=1,
                saved_result_id=20,
                chat_session_id="chat-1",
                status=ExportStatus.done,
                business_ids_json="[10]",
                options_json='{"photos": true}',
            ))
            db.commit()

        response = self.client.get("/api/chat/sessions/chat-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["company_analysis_task_id"], "analysis-latest")


if __name__ == "__main__":
    unittest.main()
