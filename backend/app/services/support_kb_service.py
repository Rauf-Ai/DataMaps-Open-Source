"""Локальная RAG-база знаний поддержки на Markdown и SQLite FTS5."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.support_knowledge import SupportKnowledgeChunk, SupportKnowledgeDocument


log = logging.getLogger(__name__)
settings = get_settings()

_FTS_TABLE = "support_kb_chunks_fts"
_SYNC_INTERVAL_SECONDS = 60
_sync_lock = threading.Lock()
_last_sync_at = 0.0


class KnowledgeBaseUnavailable(RuntimeError):
    """Индекс недоступен; поддержка должна безопасно эскалировать вопрос."""


class ArticleValidationError(ValueError):
    """Статья не соответствует минимальному контракту frontmatter."""


@dataclass(frozen=True)
class KnowledgeChunk:
    document_id: str
    title: str
    section: str
    content: str


@dataclass(frozen=True)
class SyncResult:
    documents: int
    chunks: int
    changed: int


@dataclass(frozen=True)
class _Article:
    document_id: str
    path: str
    title: str
    audience: str
    keywords: str
    source: str
    chunks: list[tuple[str, str]]


def default_root() -> Path:
    return Path(os.path.dirname(settings.duckdb_path)) / "support_kb"


def ensure_schema(conn: Connection) -> None:
    """Создаёт отдельную FTS5-таблицу без триггеров и внешнего content table."""
    if conn.dialect.name != "sqlite":
        raise KnowledgeBaseUnavailable("support KB requires SQLite FTS5")
    try:
        conn.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} "
            "USING fts5(title, keywords, content, tokenize='unicode61 remove_diacritics 2')"
        )
    except Exception as exc:  # pragma: no cover - depends on SQLite build
        raise KnowledgeBaseUnavailable("support KB FTS5 is unavailable") from exc


def _parse_frontmatter(raw: str, path: Path) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        raise ArticleValidationError(f"{path.name}: frontmatter is required")
    end = raw.find("\n---\n", 4)
    if end < 0:
        raise ArticleValidationError(f"{path.name}: frontmatter is not closed")
    metadata: dict[str, str] = {}
    for line in raw[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ArticleValidationError(f"{path.name}: invalid frontmatter")
        metadata[key.strip()] = value.strip().strip('"\'')
    return metadata, raw[end + 5:].strip()


def _keywords(raw: str, path: Path) -> str:
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    keywords = [item.strip().strip('"\'') for item in value.split(",") if item.strip()]
    if not keywords:
        raise ArticleValidationError(f"{path.name}: keywords must not be empty")
    return ", ".join(keywords)


def _split_chunks(body: str, title: str) -> list[tuple[str, str]]:
    """Делит Markdown по H2 и абзацам, сохраняя короткие самодостаточные куски."""
    sections: list[tuple[str, list[str]]] = []
    section = title
    lines: list[str] = []
    for line in body.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            if lines:
                sections.append((section, lines))
            section = heading.group(1)
            lines = []
        else:
            lines.append(line)
    if lines:
        sections.append((section, lines))

    chunks: list[tuple[str, str]] = []
    for section_title, section_lines in sections:
        paragraphs = [
            re.sub(r"\s+", " ", paragraph).strip()
            for paragraph in re.split(r"\n\s*\n", "\n".join(section_lines))
        ]
        current = ""
        for paragraph in paragraphs:
            if not paragraph:
                continue
            candidate = f"{current}\n\n{paragraph}".strip()
            if current and len(candidate) > 1400:
                chunks.append((section_title[:300], current))
                current = paragraph
            else:
                current = candidate
        if current:
            chunks.append((section_title[:300], current))
    if not chunks:
        raise ArticleValidationError("article body is empty")
    return chunks


def _parse_article(path: Path, root: Path) -> _Article:
    raw = path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(raw, path)
    required = ("id", "title", "audience", "keywords")
    missing = [field for field in required if not metadata.get(field)]
    if missing:
        raise ArticleValidationError(f"{path.name}: missing {', '.join(missing)}")
    document_id = metadata["id"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", document_id):
        raise ArticleValidationError(f"{path.name}: invalid id")
    if metadata["audience"] not in {"user", "admin"}:
        raise ArticleValidationError(f"{path.name}: invalid audience")
    return _Article(
        document_id=document_id,
        path=str(path.relative_to(root)),
        title=metadata["title"][:300],
        audience=metadata["audience"],
        keywords=_keywords(metadata["keywords"], path),
        source=raw,
        chunks=_split_chunks(body, metadata["title"]),
    )


def _delete_document(conn: Connection, document_id: str) -> None:
    chunk_ids = conn.execute(
        text("SELECT id FROM support_kb_chunks WHERE document_id = :document_id"),
        {"document_id": document_id},
    ).scalars().all()
    for chunk_id in chunk_ids:
        conn.execute(text(f"DELETE FROM {_FTS_TABLE} WHERE rowid = :rowid"), {"rowid": chunk_id})
    conn.execute(text("DELETE FROM support_kb_chunks WHERE document_id = :document_id"), {"document_id": document_id})
    conn.execute(text("DELETE FROM support_kb_documents WHERE id = :document_id"), {"document_id": document_id})


def _replace_document(conn: Connection, article: _Article, source_hash: str) -> None:
    existing_ids = conn.execute(
        text("SELECT id FROM support_kb_documents WHERE id = :document_id OR path = :path"),
        {"document_id": article.document_id, "path": article.path},
    ).scalars().all()
    for existing_id in set(existing_ids):
        _delete_document(conn, existing_id)

    conn.execute(
        text("""INSERT INTO support_kb_documents
            (id, path, title, audience, keywords, source_hash)
            VALUES (:id, :path, :title, :audience, :keywords, :source_hash)"""),
        {
            "id": article.document_id,
            "path": article.path,
            "title": article.title,
            "audience": article.audience,
            "keywords": article.keywords,
            "source_hash": source_hash,
        },
    )
    for ordinal, (section, content) in enumerate(article.chunks):
        result = conn.execute(
            text("""INSERT INTO support_kb_chunks (document_id, ordinal, section, content)
                VALUES (:document_id, :ordinal, :section, :content)"""),
            {"document_id": article.document_id, "ordinal": ordinal, "section": section, "content": content},
        )
        conn.execute(
            text(f"INSERT INTO {_FTS_TABLE}(rowid, title, keywords, content) "
                 "VALUES (:rowid, :title, :keywords, :content)"),
            {"rowid": result.lastrowid, "title": article.title, "keywords": article.keywords, "content": content},
        )


def sync_knowledge_base(conn: Connection, root: Path) -> SyncResult:
    """Синхронизирует только валидные Markdown-файлы; не затирает старую статью ошибкой."""
    ensure_schema(conn)
    root = Path(root)
    if not root.exists():
        raise KnowledgeBaseUnavailable("support KB source directory is missing")

    paths = sorted(root.glob("*.md"))
    current_paths = {str(path.relative_to(root)) for path in paths}
    articles: list[_Article] = []
    article_ids: set[str] = set()
    for path in paths:
        try:
            article = _parse_article(path, root)
            if article.document_id in article_ids:
                raise ArticleValidationError(f"{path.name}: duplicate article id")
            articles.append(article)
            article_ids.add(article.document_id)
        except (OSError, ArticleValidationError) as exc:
            log.warning("support knowledge base article skipped: %s", exc)

    changed = 0
    for article in articles:
        digest = sha256(article.source.encode("utf-8")).hexdigest()
        existing_hash = conn.execute(
            text("SELECT source_hash FROM support_kb_documents WHERE id = :document_id"),
            {"document_id": article.document_id},
        ).scalar_one_or_none()
        if existing_hash != digest:
            _replace_document(conn, article, digest)
            changed += 1

    stale_ids = conn.execute(
        text("SELECT id, path FROM support_kb_documents")
    ).all()
    for document_id, path in stale_ids:
        if path not in current_paths:
            _delete_document(conn, document_id)
            changed += 1

    documents = conn.execute(text("SELECT count(*) FROM support_kb_documents")).scalar_one()
    chunks = conn.execute(text("SELECT count(*) FROM support_kb_chunks")).scalar_one()
    return SyncResult(documents=int(documents), chunks=int(chunks), changed=changed)


def refresh_if_due(db: Session) -> None:
    """Периодически подхватывает правки примонтированных статей без перезапуска."""
    global _last_sync_at
    now = time.monotonic()
    if now - _last_sync_at < _SYNC_INTERVAL_SECONDS:
        return
    with _sync_lock:
        if now - _last_sync_at < _SYNC_INTERVAL_SECONDS:
            return
        try:
            sync_knowledge_base(db.connection(), default_root())
            _last_sync_at = time.monotonic()
        except (KnowledgeBaseUnavailable, OSError) as exc:
            log.error("support knowledge base refresh failed: %s", exc)


_TOKEN_RE = re.compile(r"[\w\-]+", re.UNICODE)
_WORD_ENDINGS = ("иями", "ями", "ами", "ого", "ему", "ому", "иях", "иях", "ов", "ев", "ам", "ям", "ах", "ях", "ую", "юю", "ая", "яя", "ое", "ее", "ий", "ый", "ой", "ом", "ем", "ы", "и", "а", "я", "у", "ю", "е", "о", "ь")


def _fts_query(query: str) -> str:
    variants: list[str] = []
    for word in _TOKEN_RE.findall(query.lower()):
        if len(word) < 2:
            continue
        variants.append(word)
        for ending in _WORD_ENDINGS:
            if word.endswith(ending) and len(word) - len(ending) >= 4:
                variants.append(word[:-len(ending)])
                break
    unique = list(dict.fromkeys(variants))
    return " OR ".join(f'"{term.replace(chr(34), "")}"*' for term in unique)


def retrieve(db: Session, query: str, *, limit: int = 6, audience: str = "all") -> list[KnowledgeChunk]:
    """Возвращает короткий набор проверенных статей; детали запроса в лог не пишет."""
    fts_query = _fts_query(query)
    if not fts_query:
        return []
    if audience not in {"all", "user", "admin"}:
        return []
    audience_filter = ""
    if audience == "user":
        audience_filter = " AND d.audience = 'user'"
    elif audience == "admin":
        audience_filter = " AND d.audience IN ('user', 'admin')"
    try:
        rows = db.execute(
            text(f"""SELECT d.id, d.title, c.section, c.content
                FROM {_FTS_TABLE} f
                JOIN support_kb_chunks c ON c.id = f.rowid
                JOIN support_kb_documents d ON d.id = c.document_id
                WHERE {_FTS_TABLE} MATCH :query
                {audience_filter}
                ORDER BY bm25({_FTS_TABLE}, 8.0, 5.0, 1.0)
                LIMIT :limit"""),
            {"query": fts_query, "limit": max(1, min(int(limit), 6))},
        ).all()
    except Exception as exc:
        log.error("support knowledge base retrieval failed: %s", type(exc).__name__)
        return []
    return [KnowledgeChunk(*row) for row in rows]


def format_context(chunks: Iterable[KnowledgeChunk]) -> str:
    """Помечает знания как справочные данные, не давая им переопределять правила агента."""
    selected = list(chunks)[:6]
    if not selected:
        return ""
    return "\n\n".join(
        f"Справочный материал. [Статья: {chunk.document_id}; раздел: {chunk.section}]\n{chunk.content}"
        for chunk in selected
    )
