"""Multi-source PDF downloader with 7-layer waterfall fallback.

Sources (priority order):
1. OpenAlex best_oa_location PDF (free OA)
2. arXiv direct link
3. S2 openAccessPdf
4. Unpaywall API
5. Publisher page + Chrome Cookie (IEEE/ACM/Springer)
6. Sci-Hub (3 mirrors)
7. DOI redirect fallback
"""
import hashlib
import re
import os
import asyncio
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse

from citationclaw.core.http_utils import make_async_client

DEFAULT_CACHE_DIR = Path("data/cache/pdf_cache")

# Sci-Hub mirrors
SCIHUB_MIRRORS = [
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
]

# Publisher domains that may need Chrome cookies
_PUBLISHER_DOMAINS = [
    "ieeexplore.ieee.org",
    "dl.acm.org",
    "link.springer.com",
    "www.sciencedirect.com",
    "onlinelibrary.wiley.com",
]


def _get_cookies_for_url(url: str) -> dict:
    """Try to get Chrome cookies for publisher domains. Fails silently."""
    try:
        from pycookiecheat import chrome_cookies
        parsed = urlparse(url)
        domain = parsed.netloc
        for pub_domain in _PUBLISHER_DOMAINS:
            if pub_domain in domain:
                return chrome_cookies(f"https://{pub_domain}")
    except Exception:
        pass
    return {}


def _extract_pdf_url_from_html(html: str, base_url: str) -> Optional[str]:
    """Extract PDF URL from HTML page (publisher landing pages)."""
    # Method 1: citation_pdf_url meta tag (IEEE, ACM standard)
    m = re.search(r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']', html, re.I)
    if m:
        return m.group(1)
    # Method 2: Direct PDF link patterns
    for pattern in [
        r'href=["\'](https?://[^"\']*?\.pdf[^"\']*)["\']',
        r'href=["\']([^"\']*?/pdf/[^"\']*)["\']',
        r'href=["\']([^"\']*?\.pdf[^"\']*)["\']',
    ]:
        m = re.search(pattern, html, re.I)
        if m:
            url = m.group(1)
            if url.startswith("/"):
                parsed = urlparse(base_url)
                url = f"{parsed.scheme}://{parsed.netloc}{url}"
            return url
    return None


def _extract_scihub_pdf_url(html: str, base_url: str) -> Optional[str]:
    """Extract PDF URL from Sci-Hub HTML page."""
    for pattern in [
        r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']',
        r'href=["\'](/storage/[^"\']+\.pdf[^"\']*)["\']',
        r'<embed[^>]+src=["\'](.*?\.pdf[^"\']*)["\']',
        r'<iframe[^>]+src=["\'](.*?\.pdf[^"\']*)["\']',
        r'location\.href\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']',
    ]:
        m = re.search(pattern, html, re.I)
        if m:
            url = m.group(1)
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                parsed = urlparse(base_url)
                url = f"{parsed.scheme}://{parsed.netloc}{url}"
            return url
    return None


class PDFDownloader:
    def __init__(self, cache_dir: Optional[Path] = None, email: Optional[str] = None):
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._email = email or "citationclaw@research.tool"
        self._client = make_async_client(timeout=60.0)
        self._client.follow_redirects = True

    def _cache_path(self, paper: dict) -> Path:
        key = (paper.get("doi") or paper.get("Paper_Title")
               or paper.get("title") or "unknown")
        h = hashlib.md5(key.encode()).hexdigest()
        return self._cache_dir / f"{h}.pdf"

    async def download(self, paper: dict, log=None) -> Optional[Path]:
        """Try up to 7 sources to download PDF. Returns cached path or None."""
        title = paper.get("Paper_Title", paper.get("title", "?"))[:40]
        cached = self._cache_path(paper)
        if cached.exists() and cached.stat().st_size > 0:
            if log:
                log(f"    [PDF缓存] {title}")
            return cached

        sources = self._determine_sources(paper)
        if not sources:
            if log:
                log(f"    [PDF] 无可用来源 (无pdf_url/doi): {title}")
            return None

        for source in sources:
            try:
                pdf_bytes = await source["fn"](paper)
                if pdf_bytes and len(pdf_bytes) > 1000 and pdf_bytes[:5] == b"%PDF-":
                    cached.write_bytes(pdf_bytes)
                    if log:
                        log(f"    [PDF✓] {source['name']} ({len(pdf_bytes)//1024}KB): {title}")
                    return cached
            except Exception as e:
                if log:
                    log(f"    [PDF✗] {source['name']}: {str(e)[:60]}")
                continue
        if log:
            log(f"    [PDF] 所有来源均失败: {title}")
        return None

    async def batch_download(self, papers: List[dict], concurrency: int = 10,
                             log=None) -> List[Optional[Path]]:
        sem = asyncio.Semaphore(concurrency)
        async def _dl(p):
            async with sem:
                return await self.download(p, log=log)
        return await asyncio.gather(*[_dl(p) for p in papers])

    def _determine_sources(self, paper: dict) -> List[dict]:
        """Return download sources in priority order."""
        sources = []
        # 1. OpenAlex OA PDF
        if paper.get("oa_pdf_url"):
            sources.append({"name": "openalex_oa", "fn": self._try_oa_pdf})
        # 2. arXiv direct
        pdf_url = paper.get("pdf_url", "")
        if pdf_url and "arxiv.org" in pdf_url:
            sources.append({"name": "arxiv", "fn": self._try_direct_url})
        # 3. S2/other direct PDF URL
        elif pdf_url:
            sources.append({"name": "direct", "fn": self._try_direct_url})
        # 4. Unpaywall
        doi = paper.get("doi", "")
        if doi:
            sources.append({"name": "unpaywall", "fn": self._try_unpaywall})
        # 5. Google Scholar paper_link (often points to publisher page)
        paper_link = paper.get("paper_link", "")
        if paper_link and "scholar.google" not in paper_link:
            sources.append({"name": "gs_link", "fn": self._try_paper_link})
        # 6. Publisher + Cookie
        if doi:
            sources.append({"name": "publisher", "fn": self._try_publisher_with_cookie})
        # 7. Sci-Hub
        if doi:
            sources.append({"name": "sci-hub", "fn": self._try_scihub})
        # 8. DOI redirect
        if doi:
            sources.append({"name": "doi_redirect", "fn": self._try_doi_redirect})
        return sources

    async def _try_oa_pdf(self, paper: dict) -> Optional[bytes]:
        url = paper.get("oa_pdf_url", "")
        if not url:
            return None
        resp = await self._client.get(url)
        if resp.status_code == 200 and resp.content[:5] == b"%PDF-":
            return resp.content
        return None

    async def _try_direct_url(self, paper: dict) -> Optional[bytes]:
        url = paper.get("pdf_url", "")
        if not url:
            return None
        resp = await self._client.get(url)
        if resp.status_code == 200 and resp.content[:5] == b"%PDF-":
            return resp.content
        return None

    async def _try_paper_link(self, paper: dict) -> Optional[bytes]:
        """Try Google Scholar's paper_link — often a direct publisher URL."""
        url = paper.get("paper_link", "")
        if not url or "scholar.google" in url:
            return None
        try:
            cookies = _get_cookies_for_url(url)
            resp = await self._client.get(url, cookies=cookies)
            if resp.status_code != 200:
                return None
            # Direct PDF
            if resp.content[:5] == b"%PDF-":
                return resp.content
            # Try to extract PDF link from HTML
            html = resp.text
            pdf_url = _extract_pdf_url_from_html(html, str(resp.url))
            if pdf_url:
                pdf_resp = await self._client.get(pdf_url, cookies=cookies)
                if pdf_resp.status_code == 200 and pdf_resp.content[:5] == b"%PDF-":
                    return pdf_resp.content
        except Exception:
            pass
        return None

    async def _try_unpaywall(self, paper: dict) -> Optional[bytes]:
        doi = paper.get("doi", "")
        if not doi:
            return None
        # Clean DOI: remove https://doi.org/ prefix if present
        doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
        url = f"https://api.unpaywall.org/v2/{doi}?email={self._email}"
        resp = await self._client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        best_oa = data.get("best_oa_location") or {}
        pdf_url = best_oa.get("url_for_pdf", "")
        if not pdf_url:
            return None
        pdf_resp = await self._client.get(pdf_url)
        if pdf_resp.status_code == 200 and pdf_resp.content[:5] == b"%PDF-":
            return pdf_resp.content
        return None

    async def _try_publisher_with_cookie(self, paper: dict) -> Optional[bytes]:
        """Try DOI landing page with Chrome cookie injection."""
        doi = paper.get("doi", "")
        if not doi:
            return None
        doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
        landing_url = f"https://doi.org/{doi}"
        try:
            resp = await self._client.get(landing_url)
            if resp.status_code != 200:
                return None
            # If direct PDF
            if resp.content[:5] == b"%PDF-":
                return resp.content
            # Parse HTML for PDF link
            html = resp.text
            pdf_url = _extract_pdf_url_from_html(html, str(resp.url))
            if not pdf_url:
                return None
            # Try with cookies
            cookies = _get_cookies_for_url(pdf_url)
            pdf_resp = await self._client.get(pdf_url, cookies=cookies)
            if pdf_resp.status_code == 200 and pdf_resp.content[:5] == b"%PDF-":
                return pdf_resp.content
        except Exception:
            pass
        return None

    async def _try_scihub(self, paper: dict) -> Optional[bytes]:
        """Try Sci-Hub mirrors."""
        doi = paper.get("doi", "")
        if not doi:
            return None
        doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
        for mirror in SCIHUB_MIRRORS:
            try:
                url = f"{mirror}/{doi}"
                resp = await self._client.get(url, timeout=20)
                if resp.status_code != 200:
                    continue
                # Direct PDF
                if resp.content[:5] == b"%PDF-":
                    return resp.content
                # Parse Sci-Hub HTML
                if "html" in resp.headers.get("content-type", ""):
                    html = resp.text
                    if "不可用" in html or "not available" in html.lower():
                        continue
                    pdf_url = _extract_scihub_pdf_url(html, str(resp.url))
                    if pdf_url:
                        pdf_resp = await self._client.get(pdf_url, timeout=30)
                        if pdf_resp.status_code == 200 and pdf_resp.content[:5] == b"%PDF-":
                            return pdf_resp.content
            except Exception:
                continue
        return None

    async def _try_doi_redirect(self, paper: dict) -> Optional[bytes]:
        doi = paper.get("doi", "")
        if not doi:
            return None
        doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
        try:
            resp = await self._client.get(f"https://doi.org/{doi}")
            if resp.status_code == 200 and resp.content[:5] == b"%PDF-":
                return resp.content
        except Exception:
            pass
        return None

    async def close(self):
        await self._client.aclose()
