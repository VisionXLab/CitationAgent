"""
持久化施引论文作者信息缓存。

跨多次运行复用已搜索的：
  - 作者-单位信息 (Searched Author-Affiliation)
  - 详细作者信息 (Searched Author Information)
  - 第一作者机构/国家
  - 作者校验结果 (Author Verification, 可选)
  - 知名学者筛选结果 (Renowned Scholar / Formated Renowned Scholar, 可选)

缓存文件：data/cache/author_info_cache.json
缓存 key：优先使用论文链接 (Paper_Link)，无链接时 fallback 到论文标题（小写）。
缓存永久有效，由用户手动清除缓存文件来重置。
"""
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

DEFAULT_CACHE_FILE = Path("data/cache/author_info_cache.json")

# 可缓存的字段名集合（按搜索顺序排列）
CACHEABLE_FIELDS = [
    "Searched Author-Affiliation",
    "First_Author_Institution",
    "First_Author_Country",
    "Searched Author Information",
    "Author Verification",
    "Renowned Scholar",
    "Formated Renowned Scholar",
]


class AuthorInfoCache:
    """跨运行持久化施引论文作者信息缓存。"""

    WRITE_EVERY = 10  # 每攒满 N 条更新才写盘，崩溃最多丢失 N-1 条

    def __init__(self, cache_file: Path = DEFAULT_CACHE_FILE):
        self.cache_file = cache_file
        self._data: dict = {}
        self._lock = asyncio.Lock()
        self._hits = 0        # 本次运行命中缓存次数
        self._misses = 0      # 本次运行未命中次数
        self._updates = 0     # 本次运行写入次数
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
    def make_key(paper_link: str, paper_title: str) -> str:
        """生成稳定的缓存键：优先用论文链接，无链接时用小写标题。"""
        key = (paper_link or "").strip()
        if not key:
            key = (paper_title or "").strip().lower()
        return key

    def get(self, paper_link: str, paper_title: str) -> Optional[dict]:
        """
        查询缓存。

        Returns:
            缓存条目 dict（包含命中的字段），未命中返回 None。
        """
        key = self.make_key(paper_link, paper_title)
        entry = self._data.get(key)
        if entry:
            self._hits += 1
        else:
            self._misses += 1
        return entry

    async def update(self, paper_link: str, paper_title: str, fields: dict):
        """
        将新搜索到的字段写入缓存（增量更新，不覆盖已有字段）。

        只写入 CACHEABLE_FIELDS 中定义的字段，忽略其余无关字段。

        Args:
            paper_link:  论文链接（缓存 key 优先来源）
            paper_title: 论文标题（fallback key）
            fields:      要写入缓存的字段 dict
        """
        key = self.make_key(paper_link, paper_title)
        to_write = {k: v for k, v in fields.items() if k in CACHEABLE_FIELDS}
        if not to_write:
            return
        async with self._lock:
            entry = self._data.setdefault(key, {"paper_title": paper_title})
            entry.update(to_write)
            entry["cached_at"] = datetime.now().isoformat()
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

    def has_field(self, paper_link: str, paper_title: str, field: str) -> bool:
        """判断缓存中是否已有指定字段。"""
        entry = self._data.get(self.make_key(paper_link, paper_title))
        return bool(entry and field in entry and entry[field] is not None)

    def stats(self) -> dict:
        """返回本次运行的缓存统计信息。"""
        return {
            "total_entries": len(self._data),
            "hits": self._hits,
            "misses": self._misses,
            "updates": self._updates,
        }
