import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import ChatSession, ExportStatus, ParserTask, User
from app.routers import chat
from app.timeutils import utcnow


class ChatSearchApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        plan = {
            "searches": [{"query": "Столовые", "location": "Казань"}],
            "source": "yandex",
            "max": 50,
            "f_phone": "any",
            "f_site": "no",
            "f_franchise": "any",
            "social_nets": [],
            "reviews_min": 0,
            "reviews_max": None,
            "rating_min": 0,
            "rating_max": 5,
        }
        with self.Session() as db:
            db.add(
                User(
                    id=1,
                    email="owner@example.com",
                    password_hash="x",
                    credits_balance=100,
                    email_verified_at=utcnow(),
                )
            )
            db.add(
                ChatSession(
                    id="session-1",
                    user_id=1,
                    title="Столовые Казани",
                    search_state_json=json.dumps(plan, ensure_ascii=False),
                )
            )
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

    def test_search_creates_and_links_parser_task(self):
        with patch.object(chat.settings, "yandex_search_api_key", "test-key"), \
                patch("app.routers.chat.run_parser_task", new=AsyncMock()) as runner:
            response = self.client.post("/api/chat/sessions/session-1/search")

        self.assertEqual(response.status_code, 200)
        task_id = response.json()["task_id"]
        with self.Session() as db:
            self.assertEqual(db.get(ChatSession, "session-1").parser_task_id, task_id)
            self.assertEqual(db.get(ParserTask, task_id).kind, "live_search")
        runner.assert_awaited_once_with(task_id)

    def test_search_reports_missing_yandex_configuration(self):
        with patch.object(chat.settings, "yandex_search_api_key", ""), \
                patch("app.routers.chat.run_parser_task", new=AsyncMock()):
            response = self.client.post("/api/chat/sessions/session-1/search")

        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "MAP-201")
        self.assertIn("YANDEX_SEARCH_API_KEY", detail["hint"])

    def test_task_status_exposes_saved_result(self):
        with self.Session() as db:
            task = ParserTask(
                id="task-1",
                user_id=1,
                status=ExportStatus.done,
                progress=100,
                kind="live_search",
                source="yandex",
                params_json="{}",
                result_json='{"saved_result_id": 11}',
                org_count=8,
                credits_charged=4,
                download_token="download-token",
            )
            db.add(task)
            session = db.get(ChatSession, "session-1")
            session.parser_task_id = task.id
            db.commit()

        response = self.client.get("/api/chat/sessions/session-1/task")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["saved_result_id"], 11)
        self.assertEqual(response.json()["org_count"], 8)
        self.assertIn("download-token", response.json()["download_url"])

    def test_running_task_status_exposes_live_progress_snapshot(self):
        with self.Session() as db:
            task = ParserTask(
                id="task-live",
                user_id=1,
                status=ExportStatus.running,
                progress=40,
                kind="live_search",
                source="yandex",
                params_json='{"target_count": 5}',
                progress_json=json.dumps({
                    "found_count": 2,
                    "target_count": 5,
                    "checked_count": 17,
                    "recent_organizations": [{"name": "Клиника А", "source": "Яндекс"}],
                }, ensure_ascii=False),
            )
            db.add(task)
            session = db.get(ChatSession, "session-1")
            session.parser_task_id = task.id
            db.commit()

        response = self.client.get("/api/chat/sessions/session-1/task")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["found_count"], 2)
        self.assertEqual(response.json()["target_count"], 5)
        self.assertEqual(response.json()["checked_count"], 17)
        self.assertEqual(response.json()["recent_organizations"][0]["name"], "Клиника А")


if __name__ == "__main__":
    unittest.main()
