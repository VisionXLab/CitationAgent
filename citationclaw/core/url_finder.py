"""
通过 ScraperAPI 在 Google Scholar 搜索论文，提取"被引用次数"链接
"""
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
from typing import Optional, Callable, List


class PaperURLFinder:
    SCHOLAR_BASE = "https://scholar.google.com"

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
        key = self.api_keys[self.key_idx % len(self.api_keys)]
        self.key_idx += 1
        return key

    def _fetch(self, url: str) -> Optional[str]:
        """通过 ScraperAPI 获取页面 HTML"""
        for attempt in range(self.retry_max_attempts):
            try:
                api_key = self._next_key()
                api_url = (
                    f"http://api.scraperapi.com/"
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

    def find_citation_url(self, paper_title: str) -> Optional[str]:
        """
        搜索论文，返回 https://scholar.google.com/scholar?cites=XXX 格式链接。
        若未找到返回 None。
        """
        search_url = (
            f"{self.SCHOLAR_BASE}/scholar"
            f"?q={urllib.parse.quote(paper_title)}&hl=en"
        )
        self.log(f"[URL查找] 搜索: {paper_title}")
        html = self._fetch(search_url)
        if not html:
            self.log(f"[URL查找] 无法获取搜索结果")
            return None

        soup = BeautifulSoup(html, "html.parser")

        # 查找 cites= 链接（"Cited by X" 或"被引用次数：X"）
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "cites=" in href:
                if href.startswith("/"):
                    full_url = self.SCHOLAR_BASE + href
                elif href.startswith("http"):
                    full_url = href
                else:
                    continue
                # 确保是 scholar.google.com 域名
                if "scholar.google" in full_url or href.startswith("/"):
                    self.log(f"[URL查找] 找到引用链接: {full_url}")
                    return full_url

        self.log(f"[URL查找] 未找到引用链接（论文可能没有引用记录）")
        return None
