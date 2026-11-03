from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.services import support_kb_service


ARTICLE = """---
id: export
title: Выгрузка результатов
audience: user
keywords: [выгрузка, экспорт, скачать, xlsx]
---
# Выгрузка результатов

## Скачать готовый файл

Откройте Историю выгрузок и дождитесь статуса готовности. Ссылка на XLSX
действует ограниченное время.
"""

PRODUCT_KB_ROOT = Path(__file__).resolve().parents[2] / "data" / "support_kb"


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        support_kb_service.ensure_schema(conn)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        yield session
    engine.dispose()


def write_article(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_sync_indexes_frontmatter_and_retrieves_by_title_and_keyword(db, tmp_path):
    write_article(tmp_path / "billing.md", """---
id: billing
title: Тарифы и оплата
audience: user
keywords: [тариф, подписка, промокод]
---
# Как отключить автопродление

Откройте Кабинет и выключите автопродление до конца периода.
""")

    result = support_kb_service.sync_knowledge_base(db.connection(), tmp_path)

    assert result == support_kb_service.SyncResult(documents=1, chunks=1, changed=1)
    assert [chunk.document_id for chunk in support_kb_service.retrieve(db, "отменить подписку")] == ["billing"]


def test_sync_updates_changed_article_and_removes_deleted_article(db, tmp_path):
    path = tmp_path / "export.md"
    write_article(path, ARTICLE)
    support_kb_service.sync_knowledge_base(db.connection(), tmp_path)

    write_article(path, ARTICLE.replace("ограниченное время", "48 часов"))
    assert support_kb_service.sync_knowledge_base(db.connection(), tmp_path).changed == 1

    path.unlink()
    support_kb_service.sync_knowledge_base(db.connection(), tmp_path)

    assert support_kb_service.retrieve(db, "скачать выгрузку") == []


def test_invalid_article_keeps_last_indexed_revision(db, tmp_path):
    path = tmp_path / "export.md"
    write_article(path, ARTICLE)
    support_kb_service.sync_knowledge_base(db.connection(), tmp_path)

    write_article(path, "# Нет frontmatter")
    support_kb_service.sync_knowledge_base(db.connection(), tmp_path)

    assert support_kb_service.retrieve(db, "скачать выгрузку")


def test_format_context_caps_chunks_and_labels_reference_data():
    chunks = [
        support_kb_service.KnowledgeChunk("id", "Заголовок", f"Раздел {index}", "Текст")
        for index in range(10)
    ]

    context = support_kb_service.format_context(chunks)

    assert context.count("[Статья:") == 6
    assert "Справочный материал" in context


def test_fts_schema_can_be_created_on_fresh_sqlite_database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        support_kb_service.ensure_schema(conn)
        names = {
            row[0] for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE name = 'support_kb_chunks_fts'"),
            )
        }
    engine.dispose()

    assert names == {"support_kb_chunks_fts"}


@pytest.mark.parametrize(("query", "document_id"), [
    ("импорт CSV", "local-usage"),
    ("выгрузка XLSX", "local-usage"),
    ("резервная копия data", "local-admin"),
])
def test_product_articles_are_retrievable(db, query, document_id):
    support_kb_service.sync_knowledge_base(db.connection(), PRODUCT_KB_ROOT)

    assert document_id in {
        chunk.document_id for chunk in support_kb_service.retrieve(db, query)
    }


def test_user_retrieval_does_not_expose_admin_article(db):
    support_kb_service.sync_knowledge_base(db.connection(), PRODUCT_KB_ROOT)

    chunks = support_kb_service.retrieve(db, "резервная копия data", audience="user")

    assert "local-admin" not in {chunk.document_id for chunk in chunks}
