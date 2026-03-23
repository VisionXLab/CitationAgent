"""
持久化引用描述缓存。

跨多次运行复用已搜索的 Citing_Description，避免对同一篇论文重复调用 LLM。

缓存文件：data/cache/citing_description_cache.json
缓存 key：citing_paper_link（无则用 citing_paper_title 小写）+ "||" + citing_paper（目标论文标题小写）
缓存永久有效，由用户手动清除缓存文件来重置。
"""
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

DEFAULT_CACHE_FILE = Path("data/cache/citing_description_cache.json")


class CitingDescriptionCache:
    """跨运行持久化引用描述缓存。"""

    WRITE_EVERY = 10  # 每攒满 N 条更新才写盘，崩溃最多丢失 N-1 条

    def __init__(self, cache_file: Path = DEFAULT_CACHE_FILE):
        self.cache_file = cache_file
        self._data: dict = {}
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._updates = 0
        self._pending = 0     # 距上次写盘后的待写条数
        self._load()

    # ─── 内部 ────────────────────────────────────────────────────────────────

    def _load(self):
        """从磁盘加载缓存（同步，在初始化时调用一次）。"""
        if self.cache_file.exists():
            try:
                text = self.cache_file.read_text(encoding="utf-8")
                self._data = json.loads(text)
            except Exception:
                self._data = {}
        else:
            self._data = {}

    async def _save(self):
        """将内存数据写入磁盘（调用方须已持有 _lock）。"""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ─── 公共 API ─────────────────────────────────────────────────────────────

    @staticmethod
    def make_key(paper_link: str, paper_title: str, citing_paper: str) -> str:
        """
        生成稳定的缓存键。

        格式：<citing_paper_id>||<target_paper_title_lower>
        citing_paper_id 优先使用论文链接，无链接时用标题小写。
        """
        citing_id = (paper_link or "").strip()
        if not citing_id:
            citing_id = (paper_title or "").strip().lower()
        target = (citing_paper or "").strip().lower()
        return f"{citing_id}||{target}"

    def get(self, paper_link: str, paper_title: str, citing_paper: str) -> Optional[str]:
        """
        查询缓存。

        Returns:
            命中时返回 Citing_Description 字符串，未命中返回 None。
        """
        key = self.make_key(paper_link, paper_title, citing_paper)
        entry = self._data.get(key)
        if entry is not None:
            self._hits += 1
            return entry.get("Citing_Description")
        else:
            self._misses += 1
            return None

    async def update(
        self,
        paper_link: str,
        paper_title: str,
        citing_paper: str,
        description: str,
    ):
        """将新搜索到的引用描述写入缓存。"""
        key = self.make_key(paper_link, paper_title, citing_paper)
        async with self._lock:
            self._data[key] = {
                "paper_title": paper_title,
                "citing_paper": citing_paper,
                "Citing_Description": description,
                "cached_at": datetime.now().isoformat(),
            }
            self._updates += 1
            self._pending += 1
            if self._pending >= self.WRITE_EVERY:
                await self._save()
                self._pending = 0

    async def flush(self):
        """强制写盘（Phase 结束时调用，确保最后不足 WRITE_EVERY 条的数据也落盘）。"""
        async with self._lock:
            if self._pending > 0:
                await self._save()
                self._pending = 0

    def has_description(self, paper_link: str, paper_title: str, citing_paper: str) -> bool:
        """判断缓存中是否已有指定条目。"""
        key = self.make_key(paper_link, paper_title, citing_paper)
        entry = self._data.get(key)
        return bool(entry and entry.get("Citing_Description") is not None)

    def stats(self) -> dict:
        """返回本次运行的缓存统计信息。"""
        return {
            "total_entries": len(self._data),
            "hits": self._hits,
            "misses": self._misses,
            "updates": self._updates,
        }
