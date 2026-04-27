"""
通过 ScraperAPI 在 Google Scholar 搜索论文，提取"被引用次数"链接
"""
import asyncio
import json
import os
import tempfile
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, Callable, List

from citationclaw.app.config_manager import DATA_DIR

_URL_CACHE_FILE = DATA_DIR / "cache" / "url_finder_cache.json"


def _normalize_title_key(title: str) -> str:
    """Lowercase + collapse whitespace for stable persistent cache keys."""
    return " ".join((title or "").lower().split())


def _load_url_cache() -> dict:
    if _URL_CACHE_FILE.exists():
        try:
            return json.loads(_URL_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_url_cache(data: dict) -> None:
    _URL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(_URL_CACHE_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(_URL_CACHE_FILE))
    except BaseException:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


class PaperURLFinder:
    SCHOLAR_BASE = "https://scholar.google.com"

    # Minimum fuzzy-match ratio to accept a title match
    TITLE_MATCH_THRESHOLD = 0.6

    def __init__(
        self,
        api_keys: List[str],
        log_callback: Optional[Callable] = None,
        retry_max_attempts: int = 3,
        retry_intervals: str = "5,10,20",
        cost_tracker=None,
    ):
        self.api_keys = api_keys
        self.key_idx = 0
        self.log = log_callback or print
        self.retry_max_attempts = retry_max_attempts
        self.retry_intervals = [int(x) for x in retry_intervals.split(",")]
        self.cost_tracker = cost_tracker

    def _next_key(self) -> str:
        # 2026-04-21: fail loud instead of "integer modulo by zero".
        # Observed during a UI run when config.scraper_api_keys was
        # silently wiped to [] by a frontend save -- users saw cryptic
        # `ZeroDivisionError: integer modulo by zero` and couldn't tell
        # the real cause was "no ScraperAPI key configured".
        if not self.api_keys:
            raise RuntimeError(
                "PaperURLFinder: scraper_api_keys 列表为空。"
                "请检查 config.json 里的 scraper_api_keys 字段，"
                "或在 UI 的设置页面里重新填入 ScraperAPI key。"
                "（如果刚刚看到这条，很可能是某次 UI 保存把 key 洗空了——"
                "这个 silent-wipe 类型的 bug 今天修过一次，如果再犯请上报。）"
            )
        key = self.api_keys[self.key_idx % len(self.api_keys)]
        self.key_idx += 1
        return key

    async def _fetch(self, url: str) -> Optional[str]:
        """通过 ScraperAPI 获取页面 HTML (non-blocking)"""
        for attempt in range(self.retry_max_attempts):
            try:
                api_key = self._next_key()
                api_url = (
                    f"https://api.scraperapi.com/"
                    f"?api_key={api_key}&url={urllib.parse.quote(url, safe='')}"
                )
                resp = await asyncio.to_thread(requests.get, api_url, timeout=60)
                if resp.status_code == 200:
                    if self.cost_tracker:
                        credit_cost = int(resp.headers.get('sa-credit-cost', 0))
                        if credit_cost > 0:
                            self.cost_tracker.add_scraper_credits(credit_cost)
                    return resp.text
                self.log(f"HTTP {resp.status_code}，尝试 {attempt+1}/{self.retry_max_attempts}")
            except Exception as e:
                self.log(f"请求异常 (尝试 {attempt+1}): {e}")
            if attempt < self.retry_max_attempts - 1:
                sleep_t = self.retry_intervals[min(attempt, len(self.retry_intervals)-1)]
                await asyncio.sleep(sleep_t)
        return None

    # Keep synchronous version for backward compatibility
    def _fetch_sync(self, url: str) -> Optional[str]:
        """Synchronous fetch fallback (used when no event loop is running)."""
        for attempt in range(self.retry_max_attempts):
            try:
                api_key = self._next_key()
                api_url = (
                    f"https://api.scraperapi.com/"
                    f"?api_key={api_key}&url={urllib.parse.quote(url, safe='')}"
                )
                resp = requests.get(api_url, timeout=60)
                if resp.status_code == 200:
                    if self.cost_tracker:
                        credit_cost = int(resp.headers.get('sa-credit-cost', 0))
                        if credit_cost > 0:
                            self.cost_tracker.add_scraper_credits(credit_cost)
                    return resp.text
                self.log(f"HTTP {resp.status_code}，尝试 {attempt+1}/{self.retry_max_attempts}")
            except Exception as e:
                self.log(f"请求异常 (尝试 {attempt+1}): {e}")
            if attempt < self.retry_max_attempts - 1:
                sleep_t = self.retry_intervals[min(attempt, len(self.retry_intervals)-1)]
                time.sleep(sleep_t)
        return None

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Lowercase and strip whitespace for fuzzy comparison."""
        return " ".join(title.lower().split())

    def _title_matches(self, candidate: str, expected: str) -> bool:
        """Check if candidate title fuzzy-matches the expected title."""
        a = self._normalize_title(candidate)
        b = self._normalize_title(expected)
        ratio = SequenceMatcher(None, a, b).ratio()
        return ratio >= self.TITLE_MATCH_THRESHOLD

    async def find_citation_url(self, paper_title: str) -> Optional[str]:
        """
        搜索论文，返回 https://scholar.google.com/scholar?cites=XXX 格式链接。
        若未找到返回 None。

        After finding a cites= link, verifies that the result's title
        fuzzy-matches the input paper_title to avoid returning citations
        for a wrong paper.
        """
        key = _normalize_title_key(paper_title)
        cache = _load_url_cache()
        if key in cache and cache[key]:
            self.log(f"[URL查找] 缓存命中，跳过 Scholar: {cache[key]}")
            return cache[key]

        search_url = (
            f"{self.SCHOLAR_BASE}/scholar"
            f"?q={urllib.parse.quote(paper_title)}&hl=en"
        )
        self.log(f"[URL查找] 搜索: {paper_title}")
        html = await self._fetch(search_url)
        if not html:
            self.log(f"[URL查找] 无法获取搜索结果")
            return None

        soup = BeautifulSoup(html, "html.parser")

        # Iterate over search result blocks to find a cites= link
        # while also verifying the title of the result.
        results = soup.select('div.gs_r.gs_or.gs_scl')
        for result in results:
            # Extract title from this result
            title_tag = result.select_one('h3.gs_rt')
            if not title_tag:
                continue
            title_link = title_tag.find('a')
            result_title = title_link.get_text(strip=True) if title_link else title_tag.get_text(strip=True)

            # Find cites= link within this result
            for a in result.find_all("a", href=True):
                href = a["href"]
                if "cites=" not in href:
                    continue
                if href.startswith("/"):
                    full_url = self.SCHOLAR_BASE + href
                elif href.startswith("http"):
                    full_url = href
                else:
                    continue
                # Ensure it is a scholar.google.com domain
                if "scholar.google" not in full_url and not href.startswith("/"):
                    continue

                # Verify title match
                if result_title and not self._title_matches(result_title, paper_title):
                    self.log(
                        f"[URL查找] 标题不匹配，跳过: '{result_title[:60]}' vs '{paper_title[:60]}'"
                    )
                    continue

                self.log(f"[URL查找] 找到引用链接: {full_url}")
                self._persist_mapping(paper_title, full_url)
                return full_url

        # Fallback: scan all links (for non-standard page layouts)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "cites=" in href:
                if href.startswith("/"):
                    full_url = self.SCHOLAR_BASE + href
                elif href.startswith("http"):
                    full_url = href
                else:
                    continue
                if "scholar.google" in full_url or href.startswith("/"):
                    self.log(f"[URL查找] 找到引用链接(fallback): {full_url}")
                    self._persist_mapping(paper_title, full_url)
                    return full_url

        self.log(f"[URL查找] 未找到引用链接（论文可能没有引用记录）")
        return None

    @staticmethod
    def _persist_mapping(paper_title: str, full_url: str) -> None:
        """Persist paper title -> cites URL mapping so future runs skip Scholar."""
        try:
            cache = _load_url_cache()
            key = _normalize_title_key(paper_title)
            if cache.get(key) != full_url:
                cache[key] = full_url
                _save_url_cache(cache)
        except Exception:
            pass
