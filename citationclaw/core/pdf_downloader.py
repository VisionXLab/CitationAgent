"""Smart multi-source PDF downloader — fused from PaperRadar + CitationClaw.

Core logic ported from PaperRadar's smart_download_pdf (proven high success rate).
Added: GS sidebar PDF link, GS "all versions" scraping, MinerU Cloud parse cache.

Download priority (tried in order):
  0.  Cache (instant)
  1.  GS sidebar PDF link (direct from Google Scholar)
  2.  Unpaywall (free OA discovery — high coverage)
  3.  OpenAlex OA PDF
  4.  CVF open access (CVPR/ICCV/WACV direct URL construction)
  5.  openAccessPdf / S2 direct (non-arxiv, non-doi)
  6.  S2 API re-lookup
  7.  DBLP conference lookup (NeurIPS/ICML/ICLR/AAAI)
  8.  Sci-Hub (3 mirrors)
  9.  arXiv PDF
  10. GS paper_link + smart transform (CVF/OpenReview/MDPI/IEEE/Springer/ACL)
  11. ScraperAPI publisher download (IEEE/Springer/Elsevier — anti-bot bypass)
  12. LLM search for alternative PDF (preprints, author pages, repos)
  13. curl + socks5 + Chrome Cookie (legacy fallback)
  14. DOI redirect
  15. ScraperAPI + LLM smart fallback (last resort for unknown pages)
"""
import hashlib
import re
import os
import asyncio
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse, quote

import subprocess
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

# Friendly source labels for logging
_SOURCE_LABELS = {
    "gs_pdf": "GS侧栏PDF",
    "cvf": "CVF开放获取",
    "openaccess": "S2开放获取",
    "s2_page": "S2页面PDF",
    "dblp": "DBLP会议版",
    "scihub": "Sci-Hub",
    "arxiv": "arXiv",
    "gs_link": "GS论文链接",
    "publisher": "出版商+Cookie",
    "doi": "DOI跳转",
    "gs_versions": "GS版本页",
    "oa_pdf": "OpenAlex开放获取",
    "unpaywall": "Unpaywall",
    "scraper_smart": "ScraperAPI智能下载",
    "llm_search": "LLM搜索替代版",
    "scraper_ieee": "ScraperAPI+IEEE",
    "scraper_springer": "ScraperAPI+Springer",
    "scraper_elsevier": "ScraperAPI+Elsevier",
    "scraper_publisher": "ScraperAPI+出版商",
}

# ── Publisher detection helpers ───────────────────────────────────────
def _detect_publisher(url: str) -> str:
    """Detect publisher from URL. Returns: ieee/springer/elsevier/acm/wiley/unknown."""
    if not url:
        return "unknown"
    host = urlparse(url).netloc.lower()
    if "ieee" in host:
        return "ieee"
    if "springer" in host or "springerlink" in host:
        return "springer"
    if "sciencedirect" in host or "elsevier" in host:
        return "elsevier"
    if "acm.org" in host:
        return "acm"
    if "wiley" in host:
        return "wiley"
    return "unknown"


def _publisher_from_doi(doi: str) -> str:
    """Guess publisher from DOI prefix."""
    if not doi:
        return "unknown"
    doi_lower = doi.lower()
    if doi_lower.startswith("10.1109/"):
        return "ieee"
    if doi_lower.startswith("10.1007/"):
        return "springer"
    if doi_lower.startswith("10.1016/"):
        return "elsevier"
    if doi_lower.startswith("10.1145/"):
        return "acm"
    if doi_lower.startswith("10.1002/"):
        return "wiley"
    return "unknown"


# ScraperAPI profiles per publisher (optimized for anti-bot bypass)
_SCRAPER_PUBLISHER_PROFILES = {
    "ieee": {
        # IEEE: Cloudflare + Akamai, JS-heavy stamp page, multi-hop
        "render": "true",
        "ultra_premium": "true",
        "country_code": "us",
        # session needed for cookie persistence across stamp hops
        "keep_headers": "true",
    },
    "elsevier": {
        # ScienceDirect: PerimeterX bot detection, React SPA.
        # render=true often causes 500; premium+us is more reliable.
        # ultra_premium needed for full bypass but not all plans support it.
        "premium": "true",
        "country_code": "us",
    },
    "springer": {
        # Springer: lighter protection, residential IP usually sufficient
        "render": "true",
        "premium": "true",
        "country_code": "us",
    },
    "acm": {
        # ACM DL: moderate protection
        "render": "true",
        "premium": "true",
        "country_code": "us",
    },
    "wiley": {
        # Wiley: Cloudflare
        "render": "true",
        "ultra_premium": "true",
        "country_code": "us",
    },
    "_default": {
        # Unknown publisher: try premium + render
        "render": "true",
        "premium": "true",
        "country_code": "us",
    },
}

# ── Proxy detection (same as PaperRadar: skip socks, use HTTP) ─────────
_HTTP_PROXY = None
for _var in ["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]:
    _val = os.environ.get(_var, "")
    if _val and _val.startswith("http"):
        _HTTP_PROXY = _val
        break


# ── Chrome cookie injection ────────────────────────────────────────────
_cookie_cache: dict = {}


# Auto-detect Chrome profile with most cookies (= institution login profile)
_chrome_profile_path: Optional[str] = None


def _detect_chrome_profile() -> str:
    """Find the Chrome profile cookie file with the most IEEE cookies."""
    global _chrome_profile_path
    if _chrome_profile_path is not None:
        return _chrome_profile_path

    import glob
    chrome_dir = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if not os.path.exists(chrome_dir):
        _chrome_profile_path = ""
        return ""

    best = ""
    best_n = 0
    for cp in glob.glob(f"{chrome_dir}/*/Cookies"):
        try:
            from pycookiecheat import chrome_cookies
            n = len(chrome_cookies("https://ieeexplore.ieee.org", cookie_file=cp))
            if n > best_n:
                best_n = n
                best = cp
        except Exception:
            pass
    _chrome_profile_path = best
    return best


def _get_cookies_for_url(url: str) -> dict:
    """Get Chrome cookies for publisher domains from the best profile."""
    try:
        host = urlparse(url).netloc
        for domain in _PUBLISHER_DOMAINS:
            if domain in host:
                if domain in _cookie_cache:
                    return _cookie_cache[domain]
                from pycookiecheat import chrome_cookies
                profile = _detect_chrome_profile()
                if profile:
                    cookies = chrome_cookies(f"https://{domain}", cookie_file=profile)
                else:
                    cookies = chrome_cookies(f"https://{domain}")
                _cookie_cache[domain] = cookies
                return cookies
    except Exception:
        pass
    return {}


# SOCKS5 proxy for curl (httpx doesn't support socks5h)
_SOCKS_PROXY = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy") or ""
if not _SOCKS_PROXY.startswith("socks"):
    _SOCKS_PROXY = ""


# ── HTML PDF extraction (covers IEEE JSON pdfUrl, meta tags, etc.) ─────
def _extract_pdf_url_from_html(html: str, base_url: str) -> Optional[str]:
    """Extract PDF URL from HTML page (publisher landing pages)."""
    parsed_base = urlparse(base_url)
    base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

    def _abs(url):
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return f"{base_origin}{url}"
        return url

    # 1. citation_pdf_url meta tag (IEEE, ACM, Google Scholar standard)
    m = re.search(r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']', html, re.I)
    if m:
        return _abs(m.group(1))

    # 2. IEEE pdfUrl/stampUrl in embedded JSON
    for pat in [r'"pdfUrl"\s*:\s*"(.*?)"', r'"stampUrl"\s*:\s*"(.*?)"']:
        m = re.search(pat, html)
        if m:
            return _abs(m.group(1))

    # 3. Direct PDF link patterns
    for pat in [
        r'href=["\'](https?://[^"\']*?\.pdf[^"\']*)["\']',
        r'href=["\']([^"\']*?/pdf/[^"\']*)["\']',
        r'href=["\']([^"\']*?download[^"\']*?\.pdf[^"\']*)["\']',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            return _abs(m.group(1))

    # 4. iframe/embed src
    for pat in [
        r'<embed[^>]+src=["\'](.*?\.pdf[^"\']*)["\']',
        r'<iframe[^>]+src=["\'](.*?\.pdf[^"\']*)["\']',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            return _abs(m.group(1))

    return None


def _extract_scihub_pdf_url(html: str, base_url: str) -> Optional[str]:
    """Extract PDF URL from Sci-Hub HTML page."""
    for pat in [
        r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']',
        r'href=["\'](/storage/[^"\']+\.pdf[^"\']*)["\']',
        r'content=["\'](/storage/[^"\']+\.pdf[^"\']*)["\']',
        r'<embed[^>]+src=["\'](.*?\.pdf[^"\']*)["\']',
        r'<iframe[^>]+src=["\'](.*?\.pdf[^"\']*)["\']',
        r'<embed[^>]+src=["\']([^"\']+)["\']',
        r'<iframe[^>]+src=["\']([^"\']+)["\']',
        r'location\.href\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            url = m.group(1)
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                parsed = urlparse(base_url)
                url = f"{parsed.scheme}://{parsed.netloc}{url}"
            return url
    return None


# ── URL transform (paper page → direct PDF) ───────────────────────────
def _transform_url(url: str) -> str:
    """Transform known paper page URLs to direct PDF URLs."""
    # CVF open access
    if "openaccess.thecvf.com" in url and "/html/" in url and url.endswith(".html"):
        return url.replace("/html/", "/papers/").replace("_paper.html", "_paper.pdf")
    # OpenReview
    if "openreview.net/forum" in url:
        return url.replace("/forum?", "/pdf?")
    # ACL Anthology
    if "aclanthology.org" in url:
        if "/abs/" in url:
            return url.replace("/abs/", "/pdf/")
        if not url.endswith(".pdf"):
            return url.rstrip("/") + ".pdf"
    # arXiv
    if "arxiv.org/abs/" in url:
        return url.replace("/abs/", "/pdf/")
    # MDPI
    if "mdpi.com" in url:
        if "/htm" in url:
            return url.replace("/htm", "/pdf")
        if re.match(r'https?://www\.mdpi\.com/[\d-]+/\d+/\d+/\d+$', url):
            return url.rstrip("/") + "/pdf"
    # Springer: /article/DOI → /content/pdf/DOI.pdf
    if "link.springer.com" in url and "/article/" in url:
        m = re.search(r'/article/(10\.\d+/[^\s?#]+)', url)
        if m:
            doi = m.group(1).rstrip('/')
            return f"https://link.springer.com/content/pdf/{doi}.pdf"
    # IEEE abstract → stamp
    if "ieeexplore.ieee.org" in url and "/abstract/" in url:
        m = re.search(r'/document/(\d+)', url)
        if m:
            return f"https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber={m.group(1)}"
    # ScienceDirect: /pii/XXX → /pii/XXX/pdfft with download params
    if "sciencedirect.com" in url and "/pii/" in url and "/pdfft" not in url:
        return url.rstrip("/") + "/pdfft?isDTMRedir=true&download=true"
    # NeurIPS proceedings
    if "papers.nips.cc" in url or "proceedings.neurips.cc" in url:
        if "-Abstract" in url:
            return url.replace("-Abstract-Conference.html", "-Paper-Conference.pdf").replace("-Abstract.html", "-Paper.pdf")
    # PMLR (ICML, AISTATS)
    if "proceedings.mlr.press" in url and url.endswith(".html"):
        base = url[:-5]
        slug = base.rsplit("/", 1)[-1]
        return f"{base}/{slug}.pdf"
    # AAAI
    if "ojs.aaai.org" in url and "/article/view/" in url:
        return url
    return url


def _build_cvf_candidates(doi: str, venue: str, year, title: str, first_author: str) -> list:
    """Build CVF open-access PDF URL candidates (CVPR/ICCV/WACV)."""
    if not title:
        return []
    venue_lower = (venue or "").lower()
    doi_lower = (doi or "").lower()
    conf = None
    if "cvpr" in venue_lower or "cvpr" in doi_lower:
        conf = "CVPR"
    elif "iccv" in venue_lower or "iccv" in doi_lower:
        conf = "ICCV"
    elif "wacv" in venue_lower or "wacv" in doi_lower:
        conf = "WACV"
    if not conf or not year:
        return []
    safe_title = re.sub(r'[^a-zA-Z0-9\s\-]', '', title)
    safe_title = re.sub(r'\s+', '_', safe_title.strip())
    safe_author = re.sub(r'[^a-zA-Z]', '', first_author or "Unknown")
    base = "https://openaccess.thecvf.com"
    return [f"{base}/content/{conf}{year}/papers/{safe_author}_{safe_title}_{conf}_{year}_paper.pdf"]


# ── PDF title verification (catch wrong-paper downloads) ─────────────
def _pdf_title_matches(pdf_data: bytes, expected_title: str, threshold: float = 0.4) -> bool:
    """Quick check: does the PDF's first page contain the expected title?

    Extracts text from the first page via PyMuPDF (fast, no full parse).
    Uses word-overlap ratio to handle minor differences.
    Returns True if enough title words appear on the first page.
    Returns True (accept) if PyMuPDF is unavailable or extraction fails.
    """
    if not expected_title or len(expected_title) < 10:
        return True  # Too short to verify meaningfully
    try:
        import fitz
        import io
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        if len(doc) == 0:
            doc.close()
            return True
        first_page_text = doc[0].get_text().lower()
        doc.close()
        if not first_page_text or len(first_page_text) < 50:
            return True  # Can't verify — accept

        # Word-overlap check
        _stop = {'a', 'an', 'the', 'of', 'in', 'on', 'for', 'and', 'or', 'to',
                 'with', 'by', 'is', 'are', 'from', 'at', 'as', 'its', 'via', 'using'}
        title_words = set(re.sub(r'[^\w\s]', ' ', expected_title.lower()).split()) - _stop
        if not title_words or len(title_words) < 2:
            return True
        matched = sum(1 for w in title_words if w in first_page_text)
        ratio = matched / len(title_words)
        return ratio >= threshold
    except ImportError:
        return True  # PyMuPDF not installed — skip verification
    except Exception:
        return True  # Any error — accept the PDF (don't block downloads)


# ═══════════════════════════════════════════════════════════════════════
class PDFDownloader:
    """Smart multi-source PDF downloader with caching."""

    def __init__(self, cache_dir: Optional[Path] = None, email: Optional[str] = None,
                 scraper_api_keys: Optional[list] = None,
                 llm_api_key: str = "", llm_base_url: str = "", llm_model: str = ""):
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._email = email or "citationclaw@research.tool"
        self._scraper_keys = scraper_api_keys or []
        self._llm_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._llm_model = llm_model

    @staticmethod
    def _make_client(timeout: float = 30.0):
        """Create httpx client with HTTP proxy (skip socks5h). Ported from PaperRadar."""
        import httpx
        return httpx.AsyncClient(
            follow_redirects=True, timeout=timeout, trust_env=False,
            proxy=_HTTP_PROXY,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            },
        )

    def _cache_path(self, paper: dict) -> Path:
        key = (paper.get("doi") or paper.get("Paper_Title")
               or paper.get("title") or "unknown")
        h = hashlib.md5(key.encode()).hexdigest()
        return self._cache_dir / f"{h}.pdf"

    # ── Core: try downloading a single URL ────────────────────────────
    async def _try_url(self, client, url: str, cookies: dict = None) -> Optional[bytes]:
        """Try downloading from a URL, handling HTML pages with PDF extraction."""
        try:
            resp = await client.get(url, cookies=cookies or {})
            if resp.status_code != 200:
                return None
            if resp.content[:5] == b"%PDF-":
                return resp.content
            # HTML page → try extracting real PDF link
            if len(resp.content) > 100:
                pdf_url = _extract_pdf_url_from_html(resp.text, str(resp.url))
                if pdf_url:
                    cookies2 = _get_cookies_for_url(pdf_url)
                    resp2 = await client.get(pdf_url, cookies=cookies2)
                    if resp2.status_code == 200 and resp2.content[:5] == b"%PDF-":
                        return resp2.content
                    # IEEE stamp returns another HTML → extract again
                    if resp2.status_code == 200 and resp2.content[:5] != b"%PDF-":
                        inner = _extract_pdf_url_from_html(resp2.text, str(resp2.url))
                        if inner:
                            resp3 = await client.get(inner, cookies=cookies2)
                            if resp3.status_code == 200 and resp3.content[:5] == b"%PDF-":
                                return resp3.content
        except Exception:
            pass
        return None

    # ── curl-based publisher download (socks5h + Chrome cookies) ────────
    async def _curl_publisher_download(self, url: str) -> Optional[bytes]:
        """Download from publisher using curl with socks5h proxy + Chrome cookies.

        This bypasses httpx's socks5 limitation and Cloudflare bot detection.
        Only used for publisher domains (IEEE/Springer/ScienceDirect/ACM).
        """
        if not _SOCKS_PROXY:
            return None  # No socks proxy configured

        host = urlparse(url).netloc
        if not any(d in host for d in _PUBLISHER_DOMAINS):
            return None  # Not a publisher domain

        cookies = _get_cookies_for_url(url)
        if not cookies:
            return None

        cookie_str = '; '.join(f'{k}={v}' for k, v in cookies.items())

        def _curl(u):
            try:
                r = subprocess.run([
                    'curl', '-x', _SOCKS_PROXY, '-s', '-L',
                    '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8',
                    '-H', 'Accept-Language: en-US,en;q=0.9',
                    '-b', cookie_str,
                    u
                ], capture_output=True, timeout=30)
                return r.stdout
            except Exception:
                return None

        try:
            # Step 1: Get publisher page
            data = await asyncio.to_thread(_curl, url)
            if not data or len(data) < 500:
                return None
            if data[:5] == b"%PDF-":
                return data

            # Step 2: Extract PDF URL from HTML
            html = data.decode('utf-8', errors='ignore')
            pdf_url = _extract_pdf_url_from_html(html, url)
            if not pdf_url:
                return None

            # Step 3: Download from extracted URL
            data2 = await asyncio.to_thread(_curl, pdf_url)
            if data2 and data2[:5] == b"%PDF-":
                return data2

            # Step 4: If stamp page, extract inner URL (IEEE getPDF.jsp)
            if data2 and len(data2) > 200 and data2[:5] != b"%PDF-":
                import re as _re
                inner_html = data2.decode('utf-8', errors='ignore')
                for pat in [r'src="(https?://[^"]*getPDF[^"]*?)"',
                            r'src="(https?://[^"]*\.pdf[^"]*?)"',
                            r'"(https?://[^"]*iel[^"]*\.pdf[^"]*?)"']:
                    m = _re.search(pat, inner_html)
                    if m:
                        data3 = await asyncio.to_thread(_curl, m.group(1))
                        if data3 and data3[:5] == b"%PDF-":
                            return data3
        except Exception:
            pass
        return None

    @staticmethod
    def _publisher_label(url: str) -> str:
        """Generate a descriptive label for publisher-based download."""
        host = urlparse(url).netloc.lower()
        if "ieee" in host:
            return "IEEE+Cookie"
        if "springer" in host:
            return "Springer+Cookie"
        if "sciencedirect" in host:
            return "ScienceDirect+Cookie"
        if "acm" in host:
            return "ACM+Cookie"
        if "wiley" in host:
            return "Wiley+Cookie"
        if "doi.org" in host:
            return "DOI+Cookie"
        return "出版商+Cookie"

    # ── ScraperAPI publisher download (IEEE/Springer/Elsevier bypass) ───
    def _scraper_build_url(self, target_url: str, publisher: str,
                           session_number: Optional[int] = None) -> Optional[str]:
        """Build ScraperAPI URL with publisher-specific profile."""
        if not self._scraper_keys:
            return None
        key = self._scraper_keys[0]
        profile = _SCRAPER_PUBLISHER_PROFILES.get(
            publisher, _SCRAPER_PUBLISHER_PROFILES["_default"]
        )
        params = [f"api_key={key}", f"url={quote(target_url)}"]
        for k, v in profile.items():
            params.append(f"{k}={v}")
        if session_number is not None:
            params.append(f"session_number={session_number}")
        return "https://api.scraperapi.com?" + "&".join(params)

    async def _scraper_publisher_download(self, url: str, doi: str = "",
                                          log=None) -> Optional[bytes]:
        """Download PDF from publisher via ScraperAPI with anti-bot bypass.

        Uses publisher-specific profiles (ultra_premium, render, session)
        to handle Cloudflare, Akamai, PerimeterX protections.

        Strategy per publisher:
          IEEE:     render stamp page → extract iframe src → download PDF
          Springer: render /content/pdf/ page with residential IP
          Elsevier: render ScienceDirect → extract pdfLink from React state
          Others:   render page → extract citation_pdf_url / PDF links
        """
        if not self._scraper_keys:
            return None

        # Determine publisher from URL or DOI
        publisher = _detect_publisher(url)
        if publisher == "unknown" and doi:
            publisher = _publisher_from_doi(doi)
        if publisher == "unknown":
            return None  # Only use for known publishers (cost control)

        import random
        session_num = random.randint(100000, 999999)
        source_label = f"scraper_{publisher}"

        from citationclaw.core.http_utils import make_async_client
        client = make_async_client(timeout=90.0)

        try:
            # ── Step 1: Prepare URLs ──
            # original_url = the article page (renderable by ScraperAPI)
            # transformed_url = direct PDF endpoint (may work with session)
            original_url = url
            transformed_url = _transform_url(url)

            # ── Step 2: Render the ARTICLE PAGE (not the download URL) ──
            # ScraperAPI renders JS, bypasses WAF — we extract PDF link from result.
            # Sending a download endpoint (like pdfft) causes 500 on ScraperAPI.
            scraper_url = self._scraper_build_url(original_url, publisher, session_num)
            if not scraper_url:
                await client.aclose()
                return None

            if log:
                log(f"    [ScraperAPI] {publisher.upper()} 渲染: {original_url[:80]}...")

            resp = await client.get(scraper_url)
            if resp.status_code != 200:
                if log:
                    log(f"    [ScraperAPI] {publisher.upper()} 渲染 HTTP {resp.status_code}")
                # Don't give up yet — try transformed URL directly through ScraperAPI
                if transformed_url != original_url:
                    scraper_url2 = self._scraper_build_url(transformed_url, publisher, session_num)
                    if scraper_url2:
                        if log:
                            log(f"    [ScraperAPI] {publisher.upper()} 直接下载: {transformed_url[:80]}...")
                        resp2 = await client.get(scraper_url2)
                        if resp2.status_code == 200 and resp2.content[:5] == b"%PDF-" and len(resp2.content) > 1000:
                            await client.aclose()
                            return resp2.content
                await client.aclose()
                return None

            # Direct PDF response from rendered page?
            if resp.content[:5] == b"%PDF-" and len(resp.content) > 1000:
                await client.aclose()
                return resp.content

            html = resp.text
            if len(html) < 200:
                await client.aclose()
                return None

            # ── Step 3: Publisher-specific PDF link extraction from rendered HTML ──
            pdf_link = None

            if publisher == "ieee":
                pdf_link = self._extract_ieee_pdf(html, original_url)
            elif publisher == "elsevier":
                pdf_link = self._extract_elsevier_pdf(html, original_url)
            elif publisher == "springer":
                pdf_link = self._extract_springer_pdf(html, original_url, doi)

            # Generic fallback: citation_pdf_url, pdfUrl, etc.
            if not pdf_link:
                pdf_link = _extract_pdf_url_from_html(html, original_url)

            # Use transformed URL as fallback candidate
            if not pdf_link and transformed_url != original_url:
                pdf_link = transformed_url

            # LLM fallback for stubborn pages
            if not pdf_link and self._llm_key and len(html) > 1000:
                pdf_link = await self._llm_find_pdf_link(html, original_url)

            if not pdf_link:
                if log:
                    log(f"    [ScraperAPI] {publisher.upper()} 未找到PDF链接")
                await client.aclose()
                return None

            if log:
                log(f"    [ScraperAPI] {publisher.upper()} PDF链接: {pdf_link[:80]}...")

            # ── Step 4: Download PDF (through ScraperAPI to maintain session) ──
            # Use same session for cookie persistence (important for IEEE multi-hop)
            pdf_scraper_url = self._scraper_build_url(pdf_link, publisher, session_num)
            if pdf_scraper_url:
                pdf_resp = await client.get(pdf_scraper_url)
                if pdf_resp.status_code == 200 and pdf_resp.content[:5] == b"%PDF-":
                    if len(pdf_resp.content) > 1000:
                        await client.aclose()
                        return pdf_resp.content

                # IEEE: stamp may return another HTML with inner iframe
                if (pdf_resp.status_code == 200 and publisher == "ieee"
                        and pdf_resp.content[:5] != b"%PDF-"):
                    inner_link = self._extract_ieee_pdf(pdf_resp.text, pdf_link)
                    if inner_link:
                        inner_url = self._scraper_build_url(inner_link, publisher, session_num)
                        if inner_url:
                            inner_resp = await client.get(inner_url)
                            if (inner_resp.status_code == 200
                                    and inner_resp.content[:5] == b"%PDF-"
                                    and len(inner_resp.content) > 1000):
                                await client.aclose()
                                return inner_resp.content

            # ── Step 5: Try direct download (some PDF URLs are public) ──
            try:
                direct_resp = await client.get(pdf_link)
                if (direct_resp.status_code == 200
                        and direct_resp.content[:5] == b"%PDF-"
                        and len(direct_resp.content) > 1000):
                    await client.aclose()
                    return direct_resp.content
            except Exception:
                pass

            await client.aclose()
            return None

        except Exception as e:
            if log:
                log(f"    [ScraperAPI] {publisher.upper()} 异常: {str(e)[:60]}")
            try:
                await client.aclose()
            except Exception:
                pass
            return None

    @staticmethod
    def _extract_ieee_pdf(html: str, base_url: str) -> Optional[str]:
        """Extract PDF URL from IEEE Xplore rendered HTML.

        IEEE flow: abstract page → stamp.jsp → iframe with getPDF.jsp → iel7/*.pdf
        ScraperAPI with render=true gives us the fully rendered stamp page.
        """
        parsed = urlparse(base_url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"

        def _abs(u):
            if u.startswith("//"):
                return f"https:{u}"
            if u.startswith("/"):
                return f"{base_origin}{u}"
            return u

        # 1. Direct PDF URL in JSON config (pdfUrl / stampUrl)
        for pat in [r'"pdfUrl"\s*:\s*"(.*?)"', r'"stampUrl"\s*:\s*"(.*?)"',
                    r'"pdfPath"\s*:\s*"(.*?)"']:
            m = re.search(pat, html)
            if m:
                return _abs(m.group(1))

        # 2. iframe/embed src pointing to PDF or getPDF
        for pat in [r'<iframe[^>]+src=["\']([^"\']*(?:getPDF|\.pdf)[^"\']*)["\']',
                    r'<embed[^>]+src=["\']([^"\']*(?:getPDF|\.pdf)[^"\']*)["\']',
                    r'src=["\']([^"\']*getPDF\.jsp[^"\']*)["\']']:
            m = re.search(pat, html, re.I)
            if m:
                return _abs(m.group(1))

        # 3. Direct link to iel7/ielx7 PDF storage
        m = re.search(r'"(https?://[^"]*iel[x7][^"]*\.pdf[^"]*)"', html)
        if m:
            return m.group(1)

        # 4. citation_pdf_url meta tag
        m = re.search(r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']', html, re.I)
        if m:
            return _abs(m.group(1))

        # 5. arnumber-based stamp construction
        m = re.search(r'"arnumber"\s*:\s*"?(\d+)"?', html)
        if m:
            return f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber={m.group(1)}"

        return None

    @staticmethod
    def _extract_elsevier_pdf(html: str, base_url: str) -> Optional[str]:
        """Extract PDF URL from ScienceDirect rendered HTML.

        ScienceDirect is a React SPA. After JS render, PDF links appear in:
        - JSON state: pdfLink, linkToPdf
        - Meta tags: citation_pdf_url
        - Download button data attributes
        """
        parsed = urlparse(base_url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"

        def _abs(u):
            if u.startswith("//"):
                return f"https:{u}"
            if u.startswith("/"):
                return f"{base_origin}{u}"
            return u

        # 1. React state / JSON embedded PDF link
        for pat in [r'"pdfLink"\s*:\s*"(.*?)"',
                    r'"linkToPdf"\s*:\s*"(.*?)"',
                    r'"pdfUrl"\s*:\s*"(.*?)"',
                    r'"pdfDownloadUrl"\s*:\s*"(.*?)"']:
            m = re.search(pat, html)
            if m:
                url = m.group(1).replace('\\u002F', '/')
                return _abs(url)

        # 2. citation_pdf_url meta
        m = re.search(r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']', html, re.I)
        if m:
            return _abs(m.group(1))

        # 3. pdfft download URL pattern
        m = re.search(r'href=["\'](https?://[^"\']*?/pii/[^"\']*?/pdfft[^"\']*)["\']', html, re.I)
        if m:
            return m.group(1)

        # 4. PII-based construction if we can find the PII
        m = re.search(r'/pii/(S\d{15,})', base_url)
        if m:
            return f"https://www.sciencedirect.com/science/article/pii/{m.group(1)}/pdfft?isDTMRedir=true&download=true"

        return None

    @staticmethod
    def _extract_springer_pdf(html: str, base_url: str, doi: str = "") -> Optional[str]:
        """Extract PDF URL from Springer rendered HTML.

        Springer is simpler — /content/pdf/DOI.pdf usually works with proper IP.
        Also handles SpringerLink chapter downloads.
        """
        parsed = urlparse(base_url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"

        def _abs(u):
            if u.startswith("//"):
                return f"https:{u}"
            if u.startswith("/"):
                return f"{base_origin}{u}"
            return u

        # 1. citation_pdf_url meta
        m = re.search(r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']', html, re.I)
        if m:
            return _abs(m.group(1))

        # 2. Direct PDF link in page
        m = re.search(r'href=["\'](https?://link\.springer\.com/content/pdf/[^"\']+)["\']', html, re.I)
        if m:
            return m.group(1)

        # 3. Download PDF button link
        for pat in [r'href=["\']([^"\']*?\.pdf[^"\']*)["\'][^>]*>.*?(?:Download|PDF)',
                    r'data-article-pdf=["\']([^"\']+)["\']']:
            m = re.search(pat, html, re.I | re.S)
            if m:
                return _abs(m.group(1))

        # 4. DOI-based construction
        if doi:
            clean_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
            return f"https://link.springer.com/content/pdf/{clean_doi}.pdf"

        return None

    # ── ScraperAPI + LLM smart fallback (for stubborn publisher pages) ──
    async def _smart_scraper_download(self, url: str) -> Optional[bytes]:
        """Last-resort: use ScraperAPI to render publisher page, then find PDF link.

        ScraperAPI renders JavaScript, bypasses Cloudflare, handles cookies.
        If direct extraction fails, uses lightweight LLM to analyze the HTML.
        """
        if not self._scraper_keys:
            return None

        key = self._scraper_keys[0]
        scraper_url = (
            f"https://api.scraperapi.com?api_key={key}"
            f"&url={quote(url)}&render=true&country_code=us"
        )

        try:
            from citationclaw.core.http_utils import make_async_client
            client = make_async_client(timeout=60.0)

            resp = await client.get(scraper_url)
            if resp.status_code != 200:
                await client.aclose()
                return None

            # Direct PDF?
            if resp.content[:5] == b"%PDF-":
                await client.aclose()
                return resp.content

            html = resp.text
            if len(html) < 500:
                await client.aclose()
                return None

            # Try rule-based extraction first
            pdf_link = _extract_pdf_url_from_html(html, url)

            # If rules failed, use LLM to find the PDF download link
            if not pdf_link and self._llm_key and len(html) > 1000:
                pdf_link = await self._llm_find_pdf_link(html, url)

            if not pdf_link:
                await client.aclose()
                return None

            # Download the found PDF link (also through ScraperAPI for cookie/JS)
            pdf_scraper_url = (
                f"https://api.scraperapi.com?api_key={key}"
                f"&url={quote(pdf_link)}&render=false"
            )
            pdf_resp = await client.get(pdf_scraper_url)
            if pdf_resp.status_code == 200 and pdf_resp.content[:5] == b"%PDF-":
                await client.aclose()
                return pdf_resp.content

            # Try direct download (some PDF links don't need ScraperAPI)
            cookies = _get_cookies_for_url(pdf_link)
            pdf_resp2 = await client.get(pdf_link, cookies=cookies)
            await client.aclose()
            if pdf_resp2.status_code == 200 and pdf_resp2.content[:5] == b"%PDF-":
                return pdf_resp2.content

        except Exception:
            pass
        return None

    async def _llm_find_pdf_link(self, html: str, page_url: str) -> Optional[str]:
        """Use lightweight LLM to find PDF download link in HTML."""
        try:
            from openai import AsyncOpenAI
            from citationclaw.core.http_utils import make_async_client

            # Send only the relevant part of HTML (links, buttons, meta tags)
            import re
            # Extract all links and meta tags
            links = re.findall(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', html[:50000])
            metas = re.findall(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*>', html[:10000])
            buttons = re.findall(r'<button[^>]*>([^<]*)</button>', html[:20000])

            context = f"Page URL: {page_url}\n\nLinks found:\n"
            for href, text in links[:50]:
                if any(k in href.lower() or k in text.lower()
                       for k in ['pdf', 'download', 'full', 'view', 'access']):
                    context += f"  {text.strip()} → {href}\n"

            context += f"\nMeta tags: {metas[:10]}\nButtons: {buttons[:10]}"

            client = AsyncOpenAI(
                api_key=self._llm_key,
                base_url=self._llm_base_url.rstrip("/") + "/" if self._llm_base_url else None,
                http_client=make_async_client(timeout=15.0),
            )
            resp = await client.chat.completions.create(
                model=self._llm_model,
                messages=[{"role": "user", "content":
                    f"From this academic paper page, find the direct PDF download URL.\n\n"
                    f"{context}\n\n"
                    f"Output ONLY the URL, nothing else. If no PDF link found, output 'NONE'."}],
                temperature=0.0,
            )
            result = resp.choices[0].message.content.strip()
            if result and result != "NONE" and result.startswith("http"):
                return result
        except Exception:
            pass
        return None

    async def _llm_search_alternative_pdf(self, title: str, doi: str = "",
                                           authors: str = "", log=None) -> Optional[bytes]:
        """Use search-grounded LLM to find alternative PDF source.

        When publisher PDFs are blocked (paywall, anti-bot), uses a search-enabled
        LLM model to find freely accessible versions:
          - arXiv / preprint versions
          - Author homepage PDFs
          - University/institutional repository copies
          - ResearchGate / Academia.edu
          - Conference preprint servers

        Requires: self._llm_key + self._llm_base_url (V-API or similar).
        Uses search-grounded model (e.g. gemini-3-flash-preview-search).
        """
        if not self._llm_key:
            return None

        try:
            from openai import AsyncOpenAI
            from citationclaw.core.http_utils import make_async_client

            # Build search query
            query_parts = [f'"{title}"']
            if doi:
                query_parts.append(f"DOI: {doi}")
            if authors:
                query_parts.append(f"Authors: {authors}")
            query = " ".join(query_parts)

            # Use search-grounded model if available, otherwise fall back to configured model
            # gemini-*-search models have Google Search grounding built in
            search_model = self._llm_model
            if "search" not in search_model.lower():
                # Try to use a search-enabled variant
                for candidate in [
                    "gemini-3-flash-preview-search",
                    "gemini-2.0-flash-search",
                    "gemini-2.5-flash-preview-04-17-search",
                ]:
                    search_model = candidate
                    break

            if log:
                log(f"    [LLM搜索] 搜索替代PDF: {title[:50]}...")

            # Search-grounded models need longer timeout (they search the web)
            import httpx as _httpx
            http_client = _httpx.AsyncClient(timeout=90.0, trust_env=True)
            client = AsyncOpenAI(
                api_key=self._llm_key,
                base_url=self._llm_base_url.rstrip("/") + "/" if self._llm_base_url else None,
                http_client=http_client,
            )

            prompt = (
                f"I need to find a freely accessible PDF for this academic paper:\n"
                f"Title: {title}\n"
            )
            if doi:
                prompt += f"DOI: {doi}\n"
            if authors:
                prompt += f"Authors: {authors}\n"
            prompt += (
                f"\nSearch for this paper and find a direct PDF download URL from any of these sources:\n"
                f"1. arXiv.org preprint\n"
                f"2. Author's personal/lab homepage\n"
                f"3. University institutional repository\n"
                f"4. ResearchGate or Academia.edu\n"
                f"5. Conference preprint server\n"
                f"6. PubMed Central (PMC)\n"
                f"7. Any other open access repository\n"
                f"\nIMPORTANT: The URL must be a DIRECT link to a .pdf file or a page that serves PDF.\n"
                f"Do NOT return publisher URLs (sciencedirect.com, ieee.org, springer.com, wiley.com).\n"
                f"Do NOT return DOI URLs (doi.org).\n"
                f"\nOutput format: one URL per line, most promising first.\n"
                f"If no free PDF found, output only: NONE"
            )

            resp = await client.chat.completions.create(
                model=search_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )

            result_text = resp.choices[0].message.content.strip()

            if not result_text or result_text == "NONE":
                if log:
                    log(f"    [LLM搜索] 未找到替代PDF源")
                return None

            # Extract URLs from response
            import re
            urls = re.findall(r'https?://[^\s<>"\')\]]+', result_text)

            # Filter out publisher/DOI URLs
            _blocked_domains = ['doi.org', 'sciencedirect.com', 'ieee.org',
                                'springer.com', 'wiley.com', 'elsevier.com',
                                'acm.org', 'tandfonline.com']
            urls = [u.rstrip('.,;)') for u in urls
                    if not any(d in u.lower() for d in _blocked_domains)]

            if not urls:
                if log:
                    log(f"    [LLM搜索] 未找到可用的替代URL")
                return None

            if log:
                log(f"    [LLM搜索] 找到 {len(urls)} 个候选URL")

            # Try downloading each candidate
            dl_client = self._make_client(timeout=30.0)
            async with dl_client as c:
                for i, url in enumerate(urls[:5]):  # Try top 5
                    try:
                        if log:
                            log(f"    [LLM搜索] 尝试 ({i+1}): {url[:70]}...")
                        data = await self._try_url(c, url)
                        if data and len(data) > 1000 and data[:5] == b"%PDF-":
                            if log:
                                log(f"    [LLM搜索] 下载成功: {len(data)//1024}KB")
                            return data
                    except Exception:
                        pass

            if log:
                log(f"    [LLM搜索] 所有候选URL均失败")
            return None

        except Exception as e:
            if log:
                log(f"    [LLM搜索] 异常: {str(e)[:60]}")
            return None

    # ── Main download method (PaperRadar-style smart download) ────────
    async def download(self, paper: dict, log=None) -> Optional[Path]:
        """Smart multi-source PDF download. Returns cached path or None."""
        title = paper.get("Paper_Title", paper.get("title", "?"))[:40]
        cached = self._cache_path(paper)
        if cached.exists() and cached.stat().st_size > 0:
            if log:
                log(f"    [PDF缓存] {title}")
            return cached

        doi = (paper.get("doi") or "").replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
        pdf_url = paper.get("pdf_url") or ""
        oa_pdf_url = paper.get("oa_pdf_url") or ""
        # ArXiv ID: from metadata (Phase 2) or extracted from pdf_url
        arxiv_id = paper.get("arxiv_id") or ""
        if not arxiv_id and pdf_url and "arxiv.org" in pdf_url:
            m = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+)', pdf_url)
            if m:
                arxiv_id = m.group(1)
        paper_link = paper.get("paper_link") or ""
        gs_pdf_link = paper.get("gs_pdf_link") or ""
        s2_id = paper.get("s2_id") or ""
        venue = paper.get("venue") or ""
        year = paper.get("paper_year") or paper.get("year") or 0
        full_title = paper.get("Paper_Title") or paper.get("title") or ""

        # Ordered download attempts
        attempts = []

        def _ok(data: Optional[bytes], source: str, skip_verify: bool = False) -> bool:
            """Check if download succeeded, verify content, save to cache.

            Performs a lightweight title check on the first page to catch
            wrong-paper downloads (e.g. OpenAlex returning a mismatched OA PDF).
            skip_verify=True for trusted sources (arXiv, Sci-Hub by DOI, cache).
            """
            if not (data and len(data) > 1000 and data[:5] == b"%PDF-"):
                return False
            # ── Title verification (catch wrong-paper downloads) ──
            if not skip_verify and full_title and len(full_title) > 10:
                if not _pdf_title_matches(data, full_title):
                    if log:
                        try:
                            log(f"    [PDF SKIP] {_SOURCE_LABELS.get(source, source)} 标题不匹配，跳过: {title}")
                        except UnicodeEncodeError:
                            pass
                    return False
            cached.write_bytes(data)
            if log:
                label = _SOURCE_LABELS.get(source, source)
                try:
                    log(f"    [PDF OK] {label} ({len(data)//1024}KB): {title}")
                except UnicodeEncodeError:
                    log(f"    [PDF OK] {label} ({len(data)//1024}KB)")
            return True

        # Detect publisher early (used by multiple steps)
        _pub_from_link = _detect_publisher(paper_link)
        _pub_from_doi = _publisher_from_doi(doi)
        _is_publisher_paper = (_pub_from_link != "unknown" or _pub_from_doi != "unknown")

        try:
            async with self._make_client(timeout=45.0) as client:

                # ── 0. GS sidebar PDF link (highest priority — GS already found the PDF)
                if gs_pdf_link:
                    url = _transform_url(gs_pdf_link)
                    cookies = _get_cookies_for_url(url)
                    data = await self._try_url(client, url, cookies)
                    if _ok(data, "gs_pdf"):
                        return cached

                # ── 1. Unpaywall (moved up — best free OA discovery service)
                if doi:
                    data = await self._try_unpaywall(client, doi)
                    if _ok(data, "unpaywall"):
                        return cached

                # ── 2. OpenAlex OA PDF
                if oa_pdf_url:
                    data = await self._try_url(client, oa_pdf_url)
                    if _ok(data, "oa_pdf"):
                        return cached

                # ── 3. CVF open access (construct URL from metadata)
                first_author = ""
                authors_raw = paper.get("authors_raw") or {}
                if isinstance(authors_raw, dict):
                    for k in authors_raw:
                        m = re.match(r'author_\d+_(.*)', k)
                        if m:
                            first_author = m.group(1).split()[-1]
                            break
                cvf_urls = _build_cvf_candidates(doi, venue, year, full_title, first_author)
                for cvf_url in cvf_urls:
                    data = await self._try_url(client, cvf_url)
                    if _ok(data, "cvf"):
                        return cached

                # ── 4. openAccessPdf (non-arxiv direct link)
                if pdf_url and "arxiv.org" not in pdf_url and "doi.org" not in pdf_url:
                    data = await self._try_url(client, pdf_url)
                    if _ok(data, "openaccess"):
                        return cached

                # ── 5. S2 API lookup (PaperRadar-style: always try if we have s2_id)
                if s2_id:
                    s2_data = await self._fetch_s2_data(client, s2_id, "")
                    if s2_data:
                        s2_pdf = (s2_data.get("openAccessPdf") or {}).get("url", "")
                        if s2_pdf:
                            data = await self._try_url(client, s2_pdf)
                            if _ok(data, "s2_page"):
                                return cached
                        # Supplement: get ArXiv ID and DOI if not already set
                        ext = s2_data.get("externalIds") or {}
                        if not arxiv_id:
                            arxiv_id = ext.get("ArXiv", "")
                        if not doi:
                            doi = ext.get("DOI", "")

                # ── 6. DBLP conference lookup
                if full_title:
                    dblp_url = await self._fetch_dblp_pdf(client, full_title)
                    if dblp_url:
                        data = await self._try_url(client, dblp_url, _get_cookies_for_url(dblp_url))
                        if _ok(data, "dblp"):
                            return cached

                # ── 7. Sci-Hub
                if doi:
                    data = await self._try_scihub(client, doi)
                    if _ok(data, "scihub", skip_verify=True):
                        return cached

                # ── 8. arXiv
                if arxiv_id:
                    data = await self._try_url(client, f"https://arxiv.org/pdf/{arxiv_id}")
                    if _ok(data, "arxiv", skip_verify=True):
                        return cached

                # ── 9. GS paper_link + smart URL transform
                if paper_link and "scholar.google" not in paper_link:
                    transformed = _transform_url(paper_link)
                    cookies = _get_cookies_for_url(transformed)
                    data = await self._try_url(client, transformed, cookies)
                    if _ok(data, "gs_link"):
                        return cached
                    # If transform didn't change URL, also try original
                    if transformed != paper_link:
                        cookies2 = _get_cookies_for_url(paper_link)
                        data = await self._try_url(client, paper_link, cookies2)
                        if _ok(data, "gs_link"):
                            return cached

                # ── 10. DOI redirect (cheap attempt before expensive ScraperAPI)
                if doi:
                    doi_url = f"https://doi.org/{doi}"
                    cookies = _get_cookies_for_url(doi_url)
                    data = await self._try_url(client, doi_url, cookies)
                    if _ok(data, "doi"):
                        return cached

        except Exception:
            pass

        # ── 11. ScraperAPI publisher download (IEEE/Springer/Elsevier anti-bot bypass)
        # Uses ultra_premium/premium + render + session for JS/WAF bypass.
        # Tried on paper_link first, then DOI URL if different publisher.
        if _is_publisher_paper and self._scraper_keys:
            if paper_link and "scholar.google" not in paper_link:
                data = await self._scraper_publisher_download(paper_link, doi, log=log)
                if _ok(data, f"scraper_{_pub_from_link if _pub_from_link != 'unknown' else _pub_from_doi}"):
                    return cached

            # Also try DOI-resolved URL if paper_link didn't work
            if doi and not (paper_link and _pub_from_link != "unknown"):
                doi_url = f"https://doi.org/{doi}"
                data = await self._scraper_publisher_download(doi_url, doi, log=log)
                if _ok(data, f"scraper_{_pub_from_doi}"):
                    return cached

        # ── 12. LLM search for alternative PDF (preprints, author pages, repos)
        # Uses search-grounded model to find freely accessible versions.
        # Works for ALL users regardless of IP — finds arXiv/repo versions.
        if self._llm_key and full_title:
            # Build author hint from paper data
            _author_hint = ""
            _authors_raw = paper.get("authors_raw") or {}
            if isinstance(_authors_raw, dict):
                names = [re.sub(r'author_\d+_', '', k) for k in list(_authors_raw.keys())[:3]]
                _author_hint = ", ".join(names) if names else ""
            data = await self._llm_search_alternative_pdf(
                full_title, doi=doi, authors=_author_hint, log=log)
            if _ok(data, "llm_search"):
                return cached

        # ── 13. curl + socks5 + Chrome cookies (legacy fallback)
        try:
            if paper_link and "scholar.google" not in paper_link:
                data = await self._curl_publisher_download(paper_link)
                if _ok(data, self._publisher_label(paper_link)):
                    return cached
            if doi:
                doi_url = f"https://doi.org/{doi}"
                data = await self._curl_publisher_download(doi_url)
                if _ok(data, self._publisher_label(doi_url)):
                    return cached
        except Exception:
            pass

        # ── 14. ScraperAPI + LLM smart fallback (last resort for non-publisher pages)
        if paper_link and "scholar.google" not in paper_link and not _is_publisher_paper:
            data = await self._smart_scraper_download(paper_link)
            if data and len(data) > 1000 and data[:5] == b"%PDF-":
                cached.write_bytes(data)
                if log:
                    log(f"    [PDF OK] ScraperAPI智能下载 ({len(data)//1024}KB): {title}")
                return cached

        if log:
            log(f"    [PDF] 所有来源均失败: {title}")
        return None

    # ── Helper: fetch S2 data by ID or title ──────────────────────────
    _s2_dl_lock = asyncio.Lock()  # Serialize S2 API calls in downloader

    async def _fetch_s2_data(self, client, s2_id: str, title: str) -> Optional[dict]:
        """Get S2 paper data (openAccessPdf, externalIds) by ID or title search."""
        try:
            if s2_id:
                url = f"https://api.semanticscholar.org/graph/v1/paper/{s2_id}?fields=openAccessPdf,externalIds"
            elif title:
                url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={quote(title)}&limit=1&fields=openAccessPdf,externalIds"
            else:
                return None
            async with self._s2_dl_lock:
                await asyncio.sleep(1.1)  # S2 rate limit: 1 req/s
                resp = await client.get(url, timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if "data" in data and data["data"]:  # Search result
                return data["data"][0]
            return data  # Direct paper result
        except Exception:
            return None

    # ── Helper: DBLP PDF lookup ───────────────────────────────────────
    async def _fetch_dblp_pdf(self, client, title: str) -> Optional[str]:
        """Query DBLP API for conference paper PDF URL."""
        try:
            api_url = f"https://dblp.org/search/publ/api?q={quote(title)}&format=json&h=3"
            resp = await client.get(api_url, timeout=10)
            if resp.status_code != 200:
                return None
            hits = resp.json().get("result", {}).get("hits", {}).get("hit", [])
            title_lower = title.lower().strip().rstrip(".")
            for hit in hits:
                info = hit.get("info", {})
                hit_title = (info.get("title") or "").lower().strip().rstrip(".")
                if hit_title != title_lower and title_lower not in hit_title:
                    continue
                ee = info.get("ee")
                if not ee:
                    continue
                urls = ee if isinstance(ee, list) else [ee]
                for venue_url in urls:
                    pdf_url = _transform_url(venue_url)
                    if pdf_url != venue_url or pdf_url.endswith(".pdf"):
                        return pdf_url
        except Exception:
            pass
        return None

    # ── Helper: Sci-Hub (uses curl+socks5 since httpx can't reach it) ──
    async def _try_scihub(self, client, doi: str) -> Optional[bytes]:
        """Try Sci-Hub mirrors for DOI. Uses curl+socks5 if available."""
        for mirror in SCIHUB_MIRRORS:
            try:
                data = await self._curl_scihub(mirror, doi)
                if data and data[:5] == b"%PDF-":
                    return data
            except Exception:
                continue

        # Fallback: try httpx (works if no socks needed)
        for mirror in SCIHUB_MIRRORS:
            try:
                resp = await client.get(f"{mirror}/{doi}", timeout=15)
                if resp.status_code != 200:
                    continue
                if resp.content[:5] == b"%PDF-":
                    return resp.content
                if "html" in resp.headers.get("content-type", ""):
                    html = resp.text
                    if "不可用" in html or "not available" in html.lower():
                        continue
                    pdf_url = _extract_scihub_pdf_url(html, str(resp.url))
                    if pdf_url:
                        r2 = await client.get(pdf_url, timeout=20)
                        if r2.status_code == 200 and r2.content[:5] == b"%PDF-":
                            return r2.content
            except Exception:
                continue
        return None

    async def _curl_scihub(self, mirror: str, doi: str) -> Optional[bytes]:
        """Download from Sci-Hub via curl+socks5."""
        if not _SOCKS_PROXY:
            return None

        def _do():
            try:
                # Step 1: Get Sci-Hub page
                r = subprocess.run([
                    'curl', '-x', _SOCKS_PROXY, '-s', '-L',
                    '-H', 'User-Agent: Mozilla/5.0',
                    f'{mirror}/{doi}'
                ], capture_output=True, timeout=20)
                if not r.stdout:
                    return None
                # Direct PDF?
                if r.stdout[:5] == b"%PDF-":
                    return r.stdout
                # Parse HTML for PDF URL
                html = r.stdout.decode('utf-8', errors='ignore')
                if "不可用" in html or "not available" in html.lower():
                    return None
                pdf_url = _extract_scihub_pdf_url(html, mirror)
                if not pdf_url:
                    return None
                # Step 2: Download PDF
                r2 = subprocess.run([
                    'curl', '-x', _SOCKS_PROXY, '-s', '-L',
                    '-H', 'User-Agent: Mozilla/5.0',
                    pdf_url
                ], capture_output=True, timeout=20)
                if r2.stdout and r2.stdout[:5] == b"%PDF-":
                    return r2.stdout
            except Exception:
                pass
            return None

        return await asyncio.to_thread(_do)

    # ── Helper: Unpaywall ─────────────────────────────────────────────
    async def _try_unpaywall(self, client, doi: str) -> Optional[bytes]:
        """Try Unpaywall API."""
        try:
            url = f"https://api.unpaywall.org/v2/{doi}?email={self._email}"
            resp = await client.get(url, timeout=10)
            if resp.status_code != 200:
                return None
            best = (resp.json().get("best_oa_location") or {}).get("url_for_pdf", "")
            if best:
                r2 = await client.get(best, timeout=20)
                if r2.status_code == 200 and r2.content[:5] == b"%PDF-":
                    return r2.content
        except Exception:
            pass
        return None

    # ── Batch download ────────────────────────────────────────────────
    _PER_PAPER_TIMEOUT = 480  # 8 minutes max per paper

    async def batch_download(self, papers: List[dict], concurrency: int = 10,
                             log=None) -> List[Optional[Path]]:
        sem = asyncio.Semaphore(concurrency)
        async def _dl(p):
            title = p.get("Paper_Title", p.get("title", "?"))[:40]
            async with sem:
                try:
                    return await asyncio.wait_for(
                        self.download(p, log=log),
                        timeout=self._PER_PAPER_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    if log:
                        log(f"    [PDF超时] {self._PER_PAPER_TIMEOUT}s 放弃: {title}")
                    return None
        return await asyncio.gather(*[_dl(p) for p in papers])

    async def close(self):
        pass  # Client is created per-download via async context manager
