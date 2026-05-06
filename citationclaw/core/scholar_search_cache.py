"""Persistent cache for Phase 3 scholar search results.

Avoids re-querying the search LLM for papers already analyzed.
Cache key: paper_title (lowercased, stripped).
Cache value: list of scholar dicts [{name, tier, honors, affiliation, country, position}].
"""
import json
import asyncio
import tempfile
from pathlib import Path
from typing import Optional, List
from datetime import datetime, timezone


# Anchor cache file to CitationClaw-v2 project root so CWD changes don't
# orphan the cache (e.g. when the eval harness runs from a sibling dir).
try:
    from citationclaw.app.config_manager import DATA_DIR as _DATA_DIR
    _DEFAULT_CACHE_FILE = _DATA_DIR / "cache" / "scholar_search_cache.json"
except Exception:
    _DEFAULT_CACHE_FILE = (Path(__file__).resolve().parent.parent.parent
                           / "data" / "cache" / "scholar_search_cache.json")


class ScholarSearchCache:
    """File-based cache for scholar search results."""

    def __init__(self, cache_file: Optional[Path] = None):
        self.cache_file = cache_file or _DEFAULT_CACHE_FILE
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = self._load()
        self._lock = asyncio.Lock()
        self._dirty = 0
        self._hits = 0
        self._misses = 0

    def _load(self) -> dict:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        """Atomic write to disk."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=self.cache_file.parent,
            delete=False, encoding="utf-8"
        )
        try:
            json.dump(self._data, tmp, ensure_ascii=False, indent=2)
            tmp.close()
            Path(tmp.name).replace(self.cache_file)
        except Exception:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)

    @staticmethod
    def _make_key(paper_title: str) -> str:
        return paper_title.strip().lower()

    def get(self, paper_title: str) -> Optional[List[dict]]:
        """Get cached scholar results for a paper. Returns None on miss."""
        key = self._make_key(paper_title)
        entry = self._data.get(key)
        if entry is not None:
            self._hits += 1
            return entry.get("scholars", [])
        self._misses += 1
        return None

    async def update(self, paper_title: str, scholars: List[dict]):
        """Store scholar search results for a paper."""
        key = self._make_key(paper_title)
        async with self._lock:
            self._data[key] = {
                "scholars": scholars,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._dirty += 1
            if self._dirty >= 5:
                self._save()
                self._dirty = 0

    async def flush(self):
        """Force write to disk."""
        async with self._lock:
            if self._dirty > 0:
                self._save()
                self._dirty = 0

    def stats(self) -> dict:
        return {
            "total_entries": len(self._data),
            "hits": self._hits,
            "misses": self._misses,
        }
