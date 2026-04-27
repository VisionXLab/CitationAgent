"""Cache for Phase 2 metadata results.

File: data/cache/metadata_cache.json
Key: DOI or paper_title.lower()
Value: {authors, affiliations, h_index, citations, source, fetched_at}
"""
import json
import os
import tempfile
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# Anchor cache file to CitationClaw-v2 project root so CWD changes don't
# orphan the cache (e.g. when the eval harness runs from a sibling dir).
try:
    from citationclaw.app.config_manager import DATA_DIR as _DATA_DIR
    CACHE_FILE = _DATA_DIR / "cache" / "metadata_cache.json"
except Exception:
    CACHE_FILE = (Path(__file__).resolve().parent.parent.parent
                  / "data" / "cache" / "metadata_cache.json")
WRITE_EVERY = 10


class MetadataCache:
    def __init__(self, cache_file: Optional[Path] = None):
        self._file = cache_file or CACHE_FILE
        self._data: Dict[str, Any] = {}
        self._pending = 0
        self._lock = None
        self._stats = {"hits": 0, "misses": 0, "updates": 0}
        self._load()

    def _get_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _load(self):
        if self._file.exists():
            with open(self._file, encoding="utf-8") as f:
                self._data = json.load(f)

    def _make_key(self, doi: str, title: str) -> str:
        if doi:
            return doi.lower().strip()
        return title.lower().strip()

    async def get(self, doi: str = "", title: str = "") -> Optional[dict]:
        async with self._get_lock():
            key = self._make_key(doi, title)
            entry = self._data.get(key)
            if entry:
                self._stats["hits"] += 1
                return entry
            self._stats["misses"] += 1
            return None

    async def update(self, doi: str, title: str, metadata: dict):
        async with self._get_lock():
            key = self._make_key(doi, title)
            metadata["fetched_at"] = datetime.now(timezone.utc).isoformat()
            self._data[key] = metadata
            self._stats["updates"] += 1
            self._pending += 1
            if self._pending >= WRITE_EVERY:
                self._write()
                self._pending = 0

    async def flush(self):
        async with self._get_lock():
            if self._pending > 0:
                self._write()
                self._pending = 0

    def _write(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._file.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._file)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def stats(self) -> dict:
        return dict(self._stats)
