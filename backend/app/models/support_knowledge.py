from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SupportKnowledgeDocument(Base):
    """Источник одной статьи базы знаний поддержки."""

    __tablename__ = "support_kb_documents"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    path: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    audience: Mapped[str] = mapped_column(String(32), nullable=False)
    keywords: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )


class SupportKnowledgeChunk(Base):
    """Самодостаточный фрагмент статьи, доступный для поиска FTS5."""

    __tablename__ = "support_kb_chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_support_kb_chunk_ordinal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("support_kb_documents.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
