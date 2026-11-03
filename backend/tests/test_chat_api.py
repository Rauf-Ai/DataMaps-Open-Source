import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import ChatSession, User
from app.routers import chat
from app.timeutils import utcnow


class ChatApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            db.add_all(
                [
                    User(
                        id=1,
                        email="one@example.com",
                        password_hash="x",
                        credits_balance=100,
                        email_verified_at=utcnow(),
                    ),
                    User(
                        id=2,
                        email="two@example.com",
                        password_hash="x",
                        credits_balance=100,
                        email_verified_at=utcnow(),
                    ),
                ]
            )
            db.commit()

        self.current_user_id = 1
        app = FastAPI()
        app.include_router(chat.router)

        def override_db():
            with self.Session() as db:
                yield db

        def override_user():
            with self.Session() as db:
                return db.get(User, self.current_user_id)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        self.client = TestClient(app)

    def tearDown(self):
        self.engine.dispose()

    def test_message_refines_and_persists_session(self):
        ai_result = {
            "plan": {
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
            },
            "reasoning": "Для продажи сайта нужны компании без сайта.",
            "assistant_message": "Подобрал столовые Казани без сайта.",
            "unknown": {},
        }
        with patch(
            "app.routers.chat.refine_live_plan",
            new=AsyncMock(return_value=ai_result),
        ):
            created = self.client.post("/api/chat/sessions", json={}).json()
            response = self.client.post(
                f"/api/chat/sessions/{created['id']}/messages",
                json={"content": "Найди столовые Казани без сайта"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["plan"]["f_site"], "no")
        self.assertEqual([message["role"] for message in body["messages"]], ["user", "assistant"])
        self.assertEqual(
            self.client.get(f"/api/chat/sessions/{created['id']}").status_code,
            200,
        )

    def test_message_normalizes_object_assistant_content(self):
        ai_result = {
            "plan": {"searches": [{"query": "Кафе", "location": "Казань"}], "max": 10},
            "reasoning": "Параметры поиска обновлены.",
            "assistant_message": {
                "website": "https://example.test",
                "social_media": ["telegram"],
                "news": [],
            },
            "unknown": {},
        }
        with patch("app.routers.chat.refine_live_plan", new=AsyncMock(return_value=ai_result)):
            created = self.client.post("/api/chat/sessions", json={}).json()
            response = self.client.post(
                f"/api/chat/sessions/{created['id']}/messages",
                json={"content": "Найди кафе Казани"},
            )

        self.assertEqual(response.status_code, 200)
        content = response.json()["messages"][-1]["content"]
        self.assertIsInstance(content, str)
        self.assertIn("https://example.test", content)

    def test_other_user_cannot_read_session(self):
        created = self.client.post("/api/chat/sessions", json={}).json()
        self.current_user_id = 2

        response = self.client.get(f"/api/chat/sessions/{created['id']}")

        self.assertEqual(response.status_code, 404)

    def test_fallback_result_queues_admin_alert(self):
        ai_result = {
            "plan": {"searches": [{"query": "Кафе", "location": "Казань"}], "max": 10},
            "reasoning": "Резервный разбор.",
            "assistant_message": "Применён резервный разбор.",
            "unknown": {},
            "fallback_used": True,
            "llm_error": "LLM timeout",
        }
        with patch("app.routers.chat.refine_live_plan", new=AsyncMock(return_value=ai_result)), \
                patch("app.routers.chat.send_llm_failure_alert", new=AsyncMock()) as alert:
            created = self.client.post("/api/chat/sessions", json={}).json()
            response = self.client.post(
                f"/api/chat/sessions/{created['id']}/messages",
                json={"content": "Найди кафе Казани"},
            )

        self.assertEqual(response.status_code, 200)
        alert.assert_awaited_once()

    def test_archive_hides_session_from_history(self):
        created = self.client.post("/api/chat/sessions", json={}).json()

        self.assertEqual(
            self.client.delete(f"/api/chat/sessions/{created['id']}").status_code,
            204,
        )
        history = self.client.get("/api/chat/sessions").json()

        self.assertEqual(history, [])
        with self.Session() as db:
            self.assertIsNotNone(db.get(ChatSession, created["id"]).archived_at)


if __name__ == "__main__":
    unittest.main()
