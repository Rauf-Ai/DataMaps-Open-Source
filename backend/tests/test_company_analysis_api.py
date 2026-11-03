import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import CompanyAnalysisTask, ExportStatus, SavedResult, User
from app.routers import parser
from app.timeutils import utcnow


class CompanyAnalysisApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        with self.Session() as db:
            db.add(User(id=1, email="owner@example.com", password_hash="x", email_verified_at=utcnow()))
            db.add(SavedResult(id=20, user_id=1, title="Result", params_json="{}", row_count=1))
            db.add(CompanyAnalysisTask(
                id="analysis-1",
                user_id=1,
                saved_result_id=20,
                status=ExportStatus.done,
                progress=100,
                business_ids_json="[10]",
                options_json='{"photos": true}',
                result_json=json.dumps({"successful_count": 1, "companies": []}),
                summary_json=json.dumps({"overview": "Итог"}),
                xlsx_download_token="xlsx-token",
                zip_download_token="zip-token",
                org_count=1,
                quota_charged=1,
            ))
            db.commit()

        app = FastAPI()
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

    def test_status_exposes_preview_summary_and_both_downloads(self):
        response = self.client.get("/api/parser/company-analysis/analysis-1/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["successful_count"], 1)
        self.assertEqual(body["summary"]["overview"], "Итог")
        self.assertIn("format=xlsx", body["xlsx_download_url"])
        self.assertIn("format=zip", body["zip_download_url"])

    def test_status_tolerates_invalid_optional_json(self):
        with self.Session() as db:
            task = db.get(CompanyAnalysisTask, "analysis-1")
            task.progress_json = "not-json"
            db.commit()

        response = self.client.get("/api/parser/company-analysis/analysis-1/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["progress_details"], {})


if __name__ == "__main__":
    unittest.main()
