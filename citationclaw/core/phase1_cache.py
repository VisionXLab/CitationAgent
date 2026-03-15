"""
持久化 Phase 1 引用爬取缓存。

跨多次运行复用已爬取的引用论文列表，避免重复调用 ScraperAPI。

缓存文件：data/cache/phase1_cache.json
缓存 key：Google Scholar 引用页 URL（原始值，不做标准化）
缓存永久有效，由用户手动清除缓存文件来重置。
"""
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

DEFAULT_CACHE_FILE = Path("data/cache/phase1_cache.json")


class Phase1Cache:
    """跨运行持久化 Phase 1 引用爬取结果缓存。"""

    def __init__(self, cache_file: Path = DEFAULT_CACHE_FILE):
        self.cache_file = cache_file
        self._data: dict = {}
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._updates = 0
        self._load()

    # ─── 内部 ────────────────────────────────────────────────────────────────

    def _load(self):
        if self.cache_file.exists():
            try:
                self._data = json.loads(self.cache_file.read_text(encoding="utf-8"))
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

    @staticmethod
    def _paper_key(paper_link: str, paper_title: str) -> str:
        """生成论文去重 key：优先用链接，无链接用小写标题。"""
        key = (paper_link or "").strip()
        if not key:
            key = (paper_title or "").strip().lower()
        return key

    def _entry(self, url: str) -> dict:
        """获取或创建 URL 对应的缓存条目。"""
        if url not in self._data:
            self._data[url] = {
                "url": url,
                "complete": False,
                "mode": "normal",
                "updated_at": datetime.now().isoformat(),
                "papers": {},
                "years": {},
            }
        return self._data[url]

    # ─── 查询 ─────────────────────────────────────────────────────────────────

    def is_complete(self, url: str) -> bool:
        entry = self._data.get(url)
        if entry and entry.get("complete"):
            self._hits += 1
            return True
        self._misses += 1
        return False

    def is_year_complete(self, url: str, year: int) -> bool:
        entry = self._data.get(url, {})
        return entry.get("years", {}).get(str(year), {}).get("complete", False)

    def get_missing_years(self, url: str, all_years: list) -> list:
        """返回 all_years 中尚未完整缓存的年份列表。"""
        entry = self._data.get(url, {})
        cached_years = entry.get("years", {})
        return [y for y in all_years if not cached_years.get(str(y), {}).get("complete", False)]

    def has_papers(self, url: str) -> bool:
        entry = self._data.get(url, {})
        return bool(entry.get("papers"))

    def stats(self) -> dict:
        return {
            "total_entries": len(self._data),
            "hits": self._hits,
            "misses": self._misses,
            "updates": self._updates,
        }

    # ─── 写入 ─────────────────────────────────────────────────────────────────

    async def add_papers(self, url: str, paper_dict: dict, year: Optional[int] = None):
        """
        将一页的 paper_dict 去重写入缓存，立即落盘。

        paper_dict 格式：{"paper_0": {"paper_link": ..., "paper_title": ..., ...}, ...}
        year：仅年份遍历模式下传入，用于标记 mode="year_traverse"。
        """
        if not paper_dict:
            return
        async with self._lock:
            entry = self._entry(url)
            if year is not None:
                entry["mode"] = "year_traverse"
            papers = entry["papers"]
            for paper_data in paper_dict.values():
                link = (paper_data.get("paper_link") or "").strip()
                title = (paper_data.get("paper_title") or "").strip()
                key = self._paper_key(link, title)
                if key and key not in papers:
                    papers[key] = paper_data
            entry["updated_at"] = datetime.now().isoformat()
            self._updates += 1
            await self._save()

    async def mark_year_complete(self, url: str, year: int):
        """标记某年份已完整爬取。"""
        async with self._lock:
            entry = self._entry(url)
            entry["mode"] = "year_traverse"
            entry["years"].setdefault(str(year), {})["complete"] = True
            entry["updated_at"] = datetime.now().isoformat()
            self._updates += 1
            await self._save()

    async def mark_complete(self, url: str):
        """标记整个 URL 已完整爬取。"""
        async with self._lock:
            entry = self._entry(url)
            entry["complete"] = True
            entry["updated_at"] = datetime.now().isoformat()
            self._updates += 1
            await self._save()

    # ─── JSONL 重建 ───────────────────────────────────────────────────────────

    def build_jsonl(self, url: str) -> str:
        """
        从缓存重建 JSONL 字符串，格式与 scraper 原生输出完全一致，供 Phase 2 读取。

        每行格式：{"page_N": {"paper_dict": {10 papers}, "next_page": null}}
        每页 10 篇论文（与 Google Scholar 分页对齐）。
        """
        entry = self._data.get(url, {})
        all_papers = list(entry.get("papers", {}).values())

        page_size = 10
        lines = []
        if not all_papers:
            return ""
        for page_idx in range(0, len(all_papers), page_size):
            batch = all_papers[page_idx: page_idx + page_size]
            paper_dict = {f"paper_{i}": p for i, p in enumerate(batch)}
            record = {"paper_dict": paper_dict, "next_page": None}
            lines.append(json.dumps({f"page_{page_idx // page_size}": record}, ensure_ascii=False))

        return "\n".join(lines) + "\n"
