import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import (
    Business,
    BusinessSource,
    ParserTask,
    SavedResult,
    SavedResultItem,
    User,
)
from app.routers import live_lab, parser
from app.timeutils import utcnow


class LiveResultActionsTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            user = User(
                id=1,
                email="owner@example.com",
                password_hash="x",
                credits_balance=100,
                email_verified_at=utcnow(),
            )
            business = Business(id=10, name="Клиника", city="Москва", region="Москва")
            saved = SavedResult(id=20, user_id=1, title="Клиники", params_json="{}", row_count=1)
            db.add_all([user, business, saved])
            db.flush()
            db.add_all([
                BusinessSource(
                    business_id=10,
                    source="yandex",
                    source_id="123456789",
                    source_url="https://yandex.ru/maps/org/123456789/",
                ),
                SavedResultItem(
                    saved_result_id=20,
                    business_id=10,
                    position=1,
                    source="yandex",
                ),
            ])
            db.commit()

        app = FastAPI()
        app.include_router(live_lab.router)
        app.include_router(parser.router)

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

    def test_saved_result_exposes_source_identity_and_url(self):
        response = self.client.get("/api/live-lab/saved-results/20")

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["source_id"], "123456789")
        self.assertEqual(item["source_url"], "https://yandex.ru/maps/org/123456789/")

    def test_company_analysis_creates_task_for_selected_saved_business(self):
        with patch("app.routers.parser._require_task_slot"), \
                patch("app.routers.parser.quota_available", return_value=10), \
                patch("app.routers.parser.run_parser_task") as runner:
            response = self.client.post(
                "/api/parser/company-analysis",
                json={"saved_result_id": 20, "business_ids": [10]},
            )

        self.assertEqual(response.status_code, 200)
        task_id = response.json()["task_id"]
        with self.Session() as db:
            task = db.get(ParserTask, task_id)
            self.assertEqual(task.kind, "company_analysis")
            self.assertIn("123456789", task.params_json)
        runner.assert_called_once_with(task_id)

    def test_company_analysis_rejects_business_outside_saved_result(self):
        response = self.client.post(
            "/api/parser/company-analysis",
            json={"saved_result_id": 20, "business_ids": [999]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("не из этого результата", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
