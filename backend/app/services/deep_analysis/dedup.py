from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import hashlib
from pathlib import Path

class Deduplicator:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.seen_hashes: set[str] = set()
        self.seen_canonical_urls: set[str] = set()

    def compute_sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def should_download(self, url: str, content: bytes) -> Optional[str]:
        """Возвращает 'deduplicated', если файл уже есть, иначе None (можно качать)."""
        file_hash = self.compute_sha256(content)
        if file_hash in self.seen_hashes:
            return "deduplicated"

        canonical = url.split("?")[0].lower().rstrip("/")
        if canonical in self.seen_canonical_urls:
            return "deduplicated"

        self.seen_hashes.add(file_hash)
        self.seen_canonical_urls.add(canonical)
        return file_hash
