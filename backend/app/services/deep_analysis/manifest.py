from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import json
from datetime import datetime, timezone
from pathlib import Path

class ManifestBuilder:
    def __init__(self):
        self.entries: list[dict[str, Any]] = []

    def add_file(self, source_url: str, local_path: str, mime_type: str, size: int, sha256: str, module: str):
        self.entries.append({
            "source_url": source_url,
            "local_path": local_path,
            "mime_type": mime_type,
            "size_bytes": size,
            "sha256": sha256,
            "module": module,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })

    def save(self, dir_path: Path):
        dir_path.mkdir(parents=True, exist_ok=True)
        manifest_data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_files": len(self.entries),
            "total_size_bytes": sum(e["size_bytes"] for e in self.entries),
            "files": self.entries,
        }
        with open(dir_path / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)
