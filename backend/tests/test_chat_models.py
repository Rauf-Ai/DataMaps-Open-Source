import json
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ChatMessage, ChatSession


class ChatModelTest(unittest.TestCase):
    def test_session_and_messages_persist(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            session = ChatSession(id="session-1", user_id=7, title="Столовые Казани")
            session.search_state_json = json.dumps(
                {"searches": [{"query": "Столовые", "location": "Казань"}]}
            )
            db.add(session)
            db.add(
                ChatMessage(
                    session_id="session-1",
                    role="user",
                    content="Найди столовые Казани",
                )
            )
            db.commit()

            row = db.execute(
                select(ChatSession).where(ChatSession.id == "session-1")
            ).scalar_one()
            message = db.execute(
                select(ChatMessage).where(ChatMessage.session_id == row.id)
            ).scalar_one()

            self.assertEqual(row.title, "Столовые Казани")
            self.assertEqual(json.loads(row.search_state_json)["searches"][0]["query"], "Столовые")
            self.assertEqual(message.role, "user")
            self.assertEqual(message.content, "Найди столовые Казани")


if __name__ == "__main__":
    unittest.main()
