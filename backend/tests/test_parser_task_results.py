import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import ExportStatus, ParserTask, ParserTaskResult, User
from app.routers import parser


class ParserTaskResultsServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            db.add_all([
                User(id=1, email="owner@example.com", password_hash="x"),
                User(id=2, email="other@example.com", password_hash="x"),
                ParserTask(
                    id="owner-task", user_id=1, status=ExportStatus.running,
                    kind="live_search", source="yandex", params_json="{}",
                ),
                ParserTask(
                    id="other-task", user_id=2, status=ExportStatus.running,
                    kind="live_search", source="yandex", params_json="{}",
                ),
                ParserTask(
                    id="old-task", user_id=1, status=ExportStatus.done,
                    kind="live_search", source="yandex", params_json="{}",
                ),
            ])
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_cursor_returns_only_rows_after_the_supplied_position(self):
        from app.services.parser_task_results import append_task_results, read_task_results

        with self.Session() as db:
            append_task_results(db, "owner-task", [
                {"oid": "1", "name": "A"},
                {"oid": "2", "name": "B"},
            ])
            db.commit()
            all_rows, next_after = read_task_results(db, "owner-task", after=0)
            later_rows, later_after = read_task_results(db, "owner-task", after=1)

        self.assertEqual(all_rows, [
            {"position": 1, "oid": "1", "name": "A"},
            {"position": 2, "oid": "2", "name": "B"},
        ])
        self.assertEqual(next_after, 2)
        self.assertEqual(later_rows, [{"position": 2, "oid": "2", "name": "B"}])
        self.assertEqual(later_after, 2)

    def test_append_keeps_only_the_first_copy_of_a_card_identity(self):
        from app.services.parser_task_results import append_task_results, read_task_results

        with self.Session() as db:
            self.assertEqual(append_task_results(db, "owner-task", [
                {"oid": "1", "source": "Яндекс", "name": "A"},
                {"oid": "1", "source": "Яндекс", "name": "A renamed"},
            ]), 1)
            db.commit()
            rows, _ = read_task_results(db, "owner-task", after=0)

        self.assertEqual(rows, [{
            "position": 1, "oid": "1", "source": "Яндекс", "name": "A",
        }])

    def test_conflicting_position_is_rejected_without_breaking_cursor_delivery(self):
        """A concurrent writer cannot create an ambiguous or skipped cursor."""
        from app.services.parser_task_results import append_task_results, read_task_results

        with self.Session() as db:
            db.add(ParserTaskResult(
                task_id="owner-task", position=1, identity_key="existing",
                data_json=json.dumps({"oid": "1", "name": "A"}),
            ))
            db.commit()
            db.add(ParserTaskResult(
                task_id="owner-task", position=1, identity_key="conflict",
                data_json=json.dumps({"oid": "conflict", "name": "Conflict"}),
            ))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

            self.assertEqual(append_task_results(db, "owner-task", [{"oid": "2", "name": "B"}]), 1)
            db.commit()
            rows, next_after = read_task_results(db, "owner-task", after=0)

        self.assertEqual([item["position"] for item in rows], [1, 2])
        self.assertEqual([item["name"] for item in rows], ["A", "B"])
        self.assertEqual(next_after, 2)

    def test_replace_reconciles_streamed_rows_to_the_final_companies(self):
        from app.services.parser_task_results import (
            append_task_results,
            read_task_results,
            replace_task_results,
        )

        with self.Session() as db:
            append_task_results(db, "owner-task", [
                {"oid": "1", "name": "Discarded preliminary match"},
                {"oid": "2", "name": "Final B"},
            ])
            replace_task_results(db, "owner-task", [
                {"oid": "2", "name": "Final B"},
                {"oid": "3", "name": "Final C"},
            ])
            db.commit()
            rows, next_after = read_task_results(db, "owner-task", after=0)

        self.assertEqual(rows, [
            {"position": 1, "oid": "2", "name": "Final B"},
            {"position": 2, "oid": "3", "name": "Final C"},
        ])
        self.assertEqual(next_after, 2)


class ParserTaskResultsApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            db.add_all([
                User(id=1, email="owner@example.com", password_hash="x"),
                User(id=2, email="other@example.com", password_hash="x"),
                ParserTask(id="owner-task", user_id=1, kind="live_search", source="yandex", params_json="{}"),
                ParserTask(id="other-task", user_id=2, kind="live_search", source="yandex", params_json="{}"),
                ParserTask(id="old-task", user_id=1, kind="live_search", source="yandex", params_json="{}"),
            ])
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

    def test_results_endpoint_hides_another_users_task(self):
        response = self.client.get("/api/parser/tasks/other-task/results")

        self.assertEqual(response.status_code, 404)

    def test_results_endpoint_returns_empty_for_an_old_task_and_rejects_invalid_cursors(self):
        response = self.client.get("/api/parser/tasks/old-task/results")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": [], "next_after": 0})
        self.assertEqual(
            self.client.get("/api/parser/tasks/old-task/results?after=-1").status_code,
            422,
        )
        self.assertEqual(
            self.client.get("/api/parser/tasks/old-task/results?limit=201").status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
