import requests
import time
from urllib.parse import urlparse, parse_qs
from typing import Optional, List, Callable
from bs4 import BeautifulSoup


class ScholarProfileScraper:
    def __init__(self, api_keys: list, log_callback: Callable,
                 retry_max_attempts: int = 3, retry_intervals: str = "5,10,20"):
        self.api_keys = api_keys
        self.log_callback = log_callback
        self.retry_max_attempts = max(1, retry_max_attempts)
        self.retry_intervals = self._parse_intervals(retry_intervals)
        self._key_idx = 0

    @staticmethod
    def _parse_intervals(intervals_str: str) -> list:
        try:
            parts = [float(s.strip()) for s in intervals_str.split(',') if s.strip()]
            return parts if parts else [5.0]
        except (ValueError, AttributeError):
            return [5.0]

    def _get_retry_wait(self, attempt: int) -> float:
        if attempt < len(self.retry_intervals):
            return self.retry_intervals[attempt]
        return self.retry_intervals[-1]

    @staticmethod
    def extract_user_id(profile_url: str) -> str:
        parsed = urlparse(profile_url)
        qs = parse_qs(parsed.query)
        if 'user' not in qs or not qs['user']:
            raise ValueError(f"无法从 URL 中解析 user 参数: {profile_url}")
        return qs['user'][0]

    def _scraper_fetch(self, url: str) -> Optional[str]:
        for attempt in range(self.retry_max_attempts):
            key_idx = (self._key_idx + attempt) % len(self.api_keys)
            api_key = self.api_keys[key_idx]
            try:
                payload = {'api_key': api_key, 'url': url}
                r = requests.get('https://api.scraperapi.com/', params=payload, timeout=90)
                if r.status_code == 200:
                    self._key_idx = (key_idx + 1) % len(self.api_keys)
                    return r.text
                else:
                    self.log_callback(f"[ScholarProfile] 请求失败(尝试 {attempt+1}/{self.retry_max_attempts}), 状态码: {r.status_code}")
                    if attempt < self.retry_max_attempts - 1:
                        wait = self._get_retry_wait(attempt)
                        self.log_callback(f"[ScholarProfile] 等待 {wait:.0f}s 后重试...")
                        time.sleep(wait)
            except Exception as e:
                self.log_callback(f"[ScholarProfile] 请求错误(尝试 {attempt+1}/{self.retry_max_attempts}): {e}")
                if attempt < self.retry_max_attempts - 1:
                    wait = self._get_retry_wait(attempt)
                    time.sleep(wait)
        return None

    def _parse_paper_rows(self, html: str) -> List[dict]:
        soup = BeautifulSoup(html, 'html.parser')
        papers = []
        for row in soup.select('tr.gsc_a_tr'):
            title_el = row.select_one('a.gsc_a_at')
            title = title_el.get_text(strip=True) if title_el else ''
            if not title:
                continue

            cite_el = row.select_one('a.gsc_a_ac')
            cite_text = cite_el.get_text(strip=True) if cite_el else ''
            try:
                citations = int(cite_text.replace(',', ''))
            except (ValueError, AttributeError):
                citations = 0

            year_el = row.select_one('span.gsc_a_h')
            year_text = year_el.get_text(strip=True) if year_el else ''
            try:
                year = int(year_text)
            except (ValueError, AttributeError):
                year = None

            papers.append({'title': title, 'year': year, 'citations': citations})
        return papers

    def fetch_all_papers(self, profile_url: str) -> List[dict]:
        user_id = self.extract_user_id(profile_url)
        base = "https://scholar.google.com/citations"
        all_papers = []
        cstart = 0

        self.log_callback(f"[ScholarProfile] 开始爬取 user={user_id} 的论文列表")
        while True:
            url = f"{base}?user={user_id}&sortby=citations&cstart={cstart}&pagesize=100"
            self.log_callback(f"[ScholarProfile] 获取第 {cstart//100 + 1} 页 (cstart={cstart})")
            html = self._scraper_fetch(url)
            if not html:
                self.log_callback(f"[ScholarProfile] 获取页面失败，停止分页")
                break
            batch = self._parse_paper_rows(html)
            self.log_callback(f"[ScholarProfile] 本页解析到 {len(batch)} 篇论文")
            all_papers.extend(batch)
            if len(batch) < 100:
                break
            cstart += 100

        all_papers.sort(key=lambda p: p['citations'], reverse=True)
        self.log_callback(f"[ScholarProfile] 共爬取到 {len(all_papers)} 篇论文")
        return all_papers
