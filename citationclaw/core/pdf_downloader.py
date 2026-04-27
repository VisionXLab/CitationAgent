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
  9.  arXiv PDF (by ID, or title search if no ID)
  9b. OpenReview title search (ICLR/NeurIPS/ICML workshops)
  10. GS paper_link + smart transform (CVF/OpenReview/MDPI/IEEE/Springer/ACL)
  11. ScraperAPI publisher download (IEEE/Springer/Elsevier — anti-bot bypass)
  12. CDP browser session (IEEE/Elsevier — real browser with auth)
  13. LLM search for alternative PDF (preprints, author pages, repos)
  14. curl + socks5 + Chrome Cookie (legacy fallback)
  15. DOI redirect
  16. ScraperAPI + LLM smart fallback (last resort for unknown pages)
"""
import hashlib
import json  # required by CDP helpers (_cdp_check_connection, _cdp_open_page,
             # _cdp_call, etc.). WITHOUT this import every CDP function
             # silently raises NameError inside its blanket try/except and
             # returns False/{}, making CDP appear "never connected" even
             # when the debug browser is alive on the port. Silent failure
             # mode — do not remove.
import re
import os
import asyncio
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse, quote

import subprocess
# Anchor cache dir to the CitationClaw-v2 project root (absolute path) so it
# stays stable regardless of the process CWD. Previously this was a relative
# path, causing the harness (CWD=eval_toolkit/phase12_harness/) to write PDFs
# into a sibling directory and `PDF_Path` strings stored in merged_authors.jsonl
# to become unreachable from any other working directory.
try:
    from citationclaw.app.config_manager import DATA_DIR as _DATA_DIR
    DEFAULT_CACHE_DIR = _DATA_DIR / "cache" / "pdf_cache"
    # CDP debug browser profile dir — anchor alongside DATA_DIR (under project
    # root's `runtime/`). Previously this was `Path("runtime/debug_browser_profile")`
    # which resolved against the process CWD, so the harness (run from
    # `eval_toolkit/phase12_harness/`) would create a SIBLING profile and any
    # publisher cookies saved via the FastAPI UI (which runs from v2 project
    # root) would not be visible to harness runs, and vice versa. Same class
    # of bug as the 2026-04-19 DEFAULT_CACHE_DIR fix.
    DEBUG_BROWSER_PROFILE_DIR = _DATA_DIR.parent / "runtime" / "debug_browser_profile"
except Exception:
    # Fallback: resolve relative to this file (...CitationClaw-v2/citationclaw/core/pdf_downloader.py)
    _V2_ROOT = Path(__file__).resolve().parent.parent.parent
    DEFAULT_CACHE_DIR = _V2_ROOT / "data" / "cache" / "pdf_cache"
    DEBUG_BROWSER_PROFILE_DIR = _V2_ROOT / "runtime" / "debug_browser_profile"

# Sci-Hub mirrors (expanded 2026 list — some original domains are now unreliable)
SCIHUB_MIRRORS = [
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
    "https://sci-hub.ren",
    "https://sci-hub.wf",
    "https://sci-hub.mksa.top",
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
    "scraper_acm": "ScraperAPI+ACM",
    "scraper_wiley": "ScraperAPI+Wiley",
    "scraper_tandf": "ScraperAPI+T&F",      # 2026-04-21
    "scraper_sage": "ScraperAPI+SAGE",      # 2026-04-21
    "scraper_publisher": "ScraperAPI+出版商",
    "openreview": "OpenReview",
    "arxiv_search": "arXiv(搜索)",
    "cdp_ieee": "CDP-IEEE",
    "cdp_elsevier": "CDP-Elsevier",
    "gs_versions_pdf": "GS所有版本(PDF直链)",
    "gs_versions_link": "GS所有版本(主链接)",
    "core": "CORE聚合器",
    "researchgate": "ResearchGate",
}

# ── Publisher detection helpers ───────────────────────────────────────
def _detect_publisher(url: str) -> str:
    """Detect publisher from URL.

    Returns one of: ieee / springer / elsevier / acm / wiley / tandf /
    sage / unknown.
    """
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
    # 2026-04-21: added after a UI run failed on
    # doi=10.1080/24751839.2024.2367387 (T&F Journal of Info & Telecom)
    # with every datacenter-IP tier hitting 403. T&F uses Cloudflare +
    # strong datacenter blocking.
    if "tandfonline" in host or "tandf" in host:
        return "tandf"
    # SAGE is another common academic publisher with similar blocking.
    if "sagepub" in host:
        return "sage"
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
    # 2026-04-21: Taylor & Francis / SAGE. Both are common publishers
    # whose datacenter-IP blocks were causing `[PDF失败]` blocks with
    # all free tiers 403.
    if doi_lower.startswith("10.1080/"):
        return "tandf"
    if doi_lower.startswith("10.1177/"):
        return "sage"
    # arXiv DOI prefix (10.48550/arXiv.<id>). Observed in today's run:
    # "PoolNet+" paper had DOI=10.48550/arxiv.2512.05362 and failed
    # because no cascade tier could extract the arxiv_id from the DOI.
    # Recognized here so the caller can pull arxiv_id directly -- the
    # arXiv tier then resolves it to a PDF trivially.
    if doi_lower.startswith("10.48550/"):
        return "arxiv"
    return "unknown"


def _arxiv_id_from_doi(doi: str) -> Optional[str]:
    """Extract arxiv_id from a 10.48550/arXiv.<id> DOI. Returns None if
    the DOI is not in the arXiv format.

    Examples:
      "10.48550/arxiv.2512.05362"       -> "2512.05362"
      "10.48550/arXiv.2301.12345v2"     -> "2301.12345" (strip version)
      "10.48550/arxiv.cs.IR/9901005"    -> "cs.IR/9901005" (legacy)
    """
    if not doi:
        return None
    m = re.match(
        r"^10\.48550/ar[Xx]iv\.([A-Za-z]+\.[A-Za-z]+/\d+|\d{4}\.\d{4,5})(?:v\d+)?$",
        doi.strip(),
    )
    return m.group(1) if m else None


# ScraperAPI profiles per publisher (optimized for anti-bot bypass)
#
# Plan note (2026-04-20): the deployed ScraperAPI key is on the **standard
# 100k-credit plan** which does NOT support `ultra_premium`. Sending that
# flag makes ScraperAPI return HTTP 500 (observed for IEEE + Wiley + ACM in
# the 2026-04-20 harness run). All profiles therefore use `premium=true`
# (residential IP, supported on every plan) instead. When a higher-tier
# plan becomes available, re-add `ultra_premium=true` to IEEE / Elsevier
# / Wiley for the strongest Cloudflare / PerimeterX / Akamai bypass.
_SCRAPER_PUBLISHER_PROFILES = {
    "ieee": {
        # IEEE: Cloudflare + Akamai, JS-heavy stamp page, multi-hop.
        # Was `ultra_premium=true` — standard plan returns 500, so downgraded.
        "render": "true",
        "premium": "true",
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
        # ACM DL: moderate protection. dl.acm.org/doi/abs/ with render=true
        # occasionally 500s; premium alone handles most non-OA fallbacks.
        "render": "true",
        "premium": "true",
        "country_code": "us",
    },
    "wiley": {
        # Wiley: Cloudflare. Was `ultra_premium=true` — standard plan 500s.
        "render": "true",
        "premium": "true",
        "country_code": "us",
    },
    "tandf": {
        # Taylor & Francis (tandfonline.com): Cloudflare + datacenter-
        # IP blocking. Observed 2026-04-21: direct HTTP, GS PDF link,
        # DOI redirect all 403 from our residential IP → need
        # ScraperAPI residential proxies. render=true because
        # tandfonline is a SPA that injects PDF links via JS.
        "render": "true",
        "premium": "true",
        "country_code": "us",
    },
    "sage": {
        # SAGE (journals.sagepub.com): similar profile to T&F
        # (Cloudflare + strong DC blocking).
        "render": "true",
        "premium": "true",
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

    # 5. figshare / institutional repo download buttons
    #    figshare uses data-file-id or /ndownloader/ patterns
    if "figshare" in base_url:
        # Look for ndownloader link
        m = re.search(r'href=["\'](https?://[^"\']*ndownloader/files/\d+[^"\']*)["\']', html, re.I)
        if m:
            return m.group(1)
        # Look for download button with file ID
        m = re.search(r'href=["\']([^"\']*?/ndownloader/articles/\d+[^"\']*)["\']', html, re.I)
        if m:
            return _abs(m.group(1))
        # data-file-id attribute → construct ndownloader URL
        m = re.search(r'data-file-id=["\'](\d+)["\']', html)
        if m:
            return f"https://figshare.com/ndownloader/files/{m.group(1)}"

    return None


def _scihub_article_missing(html: str) -> bool:
    """Detect Sci-Hub 'article not in database' pages (multilingual)."""
    lower = html.lower()
    # Chinese, English, Russian, Spanish, Portuguese variants Sci-Hub uses
    for marker in ("不可用", "not available",
                   "статья отсутствует", "статья не найдена",
                   "article not found", "article missing",
                   "no disponible", "não disponível",
                   "aucun article"):
        if marker.lower() in lower:
            return True
    return False


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
    # figshare: /articles/... → /ndownloader/... (GS often links to figshare landing pages)
    if "figshare.com" in url or "figshare." in url:
        # figshare.com/articles/TYPE/TITLE/ID/VERSION → ndownloader/files needs file ID
        # But /articles/.../ID can be transformed to /ndownloader/articles/ID
        m = re.search(r'/articles/[^/]+/[^/]+/(\d+)', url)
        if m:
            article_id = m.group(1)
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}/ndownloader/articles/{article_id}/versions/1"
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
# Common English stopwords — excluded from word-overlap to avoid inflated
# match ratios from high-frequency words that any CS paper contains.
_TITLE_STOPWORDS = {
    'a', 'an', 'the', 'of', 'in', 'on', 'for', 'and', 'or', 'to',
    'with', 'by', 'is', 'are', 'from', 'at', 'as', 'its', 'via', 'using',
    'based', 'towards', 'toward', 'through', 'into',
    # These are too common in CV/ML paper titles — downweight them
    'network', 'networks', 'learning', 'deep', 'neural', 'model', 'models',
    'method', 'methods', 'approach', 'framework', 'system', 'analysis',
    'new', 'novel', 'efficient', 'robust', 'improved',
}


def _extract_title_identifier(title: str) -> Optional[str]:
    """Extract a distinctive leading identifier like 'ECNet', 'CADC++', 'GCA-Net'.

    Many CV papers start with NAME: Description... The NAME is nearly always
    printed verbatim on the first page, so requiring it to appear is a cheap
    but very effective way to reject wrong-paper downloads.
    """
    if not title:
        return None
    # "NAME: rest" — the colon-delimited prefix is the clearest case
    m = re.match(r'^\s*([A-Za-z0-9][\w\-+.]{1,30})\s*[:：]', title)
    if m:
        return m.group(1)
    # "NAME - rest" or "NAME — rest"
    m = re.match(r'^\s*([A-Za-z0-9][\w\-+.]{1,30})\s*[—–\-]\s+\w', title)
    if m and len(m.group(1)) <= 15:  # looser — only short tokens qualify
        return m.group(1)
    # Leading all-caps acronym of 3+ chars
    m = re.match(r'^\s*([A-Z][A-Z0-9]{2,})\b', title)
    if m:
        return m.group(1)
    # Mixed-case identifier like "ECNet", "ResNet", "MGCNet"
    m = re.match(r'^\s*([A-Z]{2,}[A-Za-z]+|[A-Z][a-z]+[A-Z][A-Za-z]+)\b', title)
    if m:
        return m.group(1)
    return None


def _pdf_bytes_are_mojibake(data: bytes) -> bool:
    """Detect text-round-trip corruption in a byte string that starts with %PDF-.

    Two variants of corruption are caught:

      (a) Hard: `response.text` (strict UTF-8 decode with replace) turned raw
          high-bit bytes into U+FFFD. Re-encoding as UTF-8 yields \\xef\\xbf\\xbd
          triplets where the original 4-byte PDF binary marker used to be.

      (b) Soft: bytes that happened to decode as Latin-1 then re-encoded as
          UTF-8 (or equivalent). Each original 0x80+ byte becomes a 2-byte
          \\xc3\\xXX pair. The PDF binary marker (usually 4 high-bit bytes on
          line 2 after the version line) becomes ~8 bytes, with \\xc3 every
          other position. Such files sometimes open in PyMuPDF but content
          streams fail zlib decode (empty page text).
    """
    if b"\xef\xbf\xbd\xef\xbf\xbd\xef\xbf\xbd" in data[:1024]:
        return True
    # Soft-mojibake: after "%PDF-X.Y\r?\n%" the marker line should be 4 raw
    # high-bit bytes (or ASCII). Mojibake makes it 6-8+ bytes with \xc3 tokens.
    import re as _re
    m = _re.match(rb"%PDF-\d+\.\d+\r?\n%([^\r\n]{1,32})\r?\n", data[:128])
    if m:
        marker = m.group(1)
        # Latin-1 -> UTF-8 signature: a run of \xc3\xXX pairs
        c3_count = marker.count(b"\xc3")
        if len(marker) >= 6 and c3_count >= 3:
            return True
    return False


def _pdf_title_matches(pdf_data: bytes, expected_title: str, threshold: float = 0.55) -> bool:
    """Does the PDF's first page match the expected paper title?

    Strictness rationale: OpenAlex/S2 frequently return *wrong* arxiv_ids for
    recent publisher papers — mapping the DOI to some semantically related
    arXiv paper. A lenient word-overlap alone cannot reject these because CV
    papers share many words ('detection', 'feature', 'object', 'network').

    Approach:
      1. Hard rule — if the title has a distinctive identifier ('ECNet:' /
         'CADC++:' / 'MGCNet:' / 'GCA-Net'), it MUST appear on the first page.
         This alone blocks the majority of OpenAlex arxiv mis-matches.
      2. Word-overlap — distinctive (non-stop-word) title tokens must appear
         at >= `threshold` ratio. Threshold auto-tightens on longer titles.

    Returns True (accept) on PyMuPDF failure to avoid blocking legitimate
    downloads when the verifier itself is broken.
    """
    if not expected_title or len(expected_title) < 10:
        return True  # Too short to verify meaningfully
    try:
        import fitz
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        if len(doc) == 0:
            doc.close()
            return True
        first_page_text = doc[0].get_text().lower()
        doc.close()
        if not first_page_text or len(first_page_text) < 50:
            return True  # Can't verify — accept

        # ── Rule 1: leading identifier must appear ─────────────────
        ident = _extract_title_identifier(expected_title)
        if ident and len(ident) >= 3:
            # Case-insensitive substring check, but respect word boundaries
            # via a regex so "GCA" doesn't match "gca" inside "gcagca"
            if not re.search(rf'\b{re.escape(ident.lower())}\b', first_page_text):
                return False

        # ── Rule 2: distinctive word overlap ──────────────────────
        clean = re.sub(r'[^\w\s]', ' ', expected_title.lower())
        all_words = [w for w in clean.split() if w]
        title_words = set(w for w in all_words if w not in _TITLE_STOPWORDS and len(w) > 2)
        if len(title_words) < 2:
            return True  # Not enough signal to verify

        matched = sum(1 for w in title_words if re.search(rf'\b{re.escape(w)}\b', first_page_text))
        ratio = matched / len(title_words)

        # Longer titles tolerate a bit less ratio (more words → more chance a
        # few random ones miss) but still demand a strong match.
        effective_threshold = 0.5 if len(title_words) > 10 else threshold
        return ratio >= effective_threshold

    except ImportError:
        return True  # PyMuPDF not installed — skip verification
    except Exception:
        return True  # Any error — accept the PDF (don't block downloads)


# ── CDP (Chrome DevTools Protocol) helpers ────────────────────────────
# Download PDFs via a live, authenticated browser session.
# Requires: websocket-client (pip install websocket-client)
#         + browser with --remote-debugging-port.
# Graceful degradation: if websocket-client not installed, CDP is skipped.

try:
    import websocket as _websocket_mod
except ImportError:
    _websocket_mod = None

# ScienceDirect pdfDownload metadata regex
_SD_PDF_DOWNLOAD_RE = re.compile(
    r'"pdfDownload":\{"isPdfFullText":(?:true|false),'
    r'"urlMetadata":\{"queryParams":\{"md5":"([^"]+)","pid":"([^"]+)"\},'
    r'"pii":"([^"]+)","pdfExtension":"([^"]+)","path":"([^"]+)"\}\}'
)


def _cdp_available() -> bool:
    return _websocket_mod is not None


_cdp_browser_launched = False  # Only auto-launch once per process


def _cdp_ensure_browser(debug_port: int) -> bool:
    """Ensure a debug browser is running. Auto-launch if needed (once per process)."""
    global _cdp_browser_launched
    if not _cdp_available():
        return False
    if _cdp_check_connection(debug_port):
        return True
    if _cdp_browser_launched:
        return False  # Already tried, don't retry

    # Auto-launch Edge or Chrome with remote debugging
    _cdp_browser_launched = True
    import platform
    if platform.system() == "Windows":
        browser_paths = [
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        ]
    elif platform.system() == "Darwin":
        browser_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        browser_paths = [
            "/usr/bin/google-chrome", "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ]

    binary = None
    for p in browser_paths:
        if Path(p).exists():
            binary = p
            break
    if not binary:
        return False

    # Use absolute profile dir anchored at v2 project root (see module-level
    # DEBUG_BROWSER_PROFILE_DIR docstring for the CWD-bug history).
    profile_dir = DEBUG_BROWSER_PROFILE_DIR
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Bypass system proxy for publisher domains so institutional IP auth works.
        # Users on campus WiFi with a proxy client (e.g. FLClash/Clash) need
        # IEEE/Elsevier to see the campus IP, not the proxy IP.
        _bypass_domains = (
            "ieeexplore.ieee.org;"
            "ieee.org;"
            "sciencedirect.com;"
            "elsevier.com;"
            "link.springer.com;"
            "dl.acm.org;"
            "onlinelibrary.wiley.com"
        )
        subprocess.Popen([
            binary,
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={profile_dir}",
            "--profile-directory=Default",
            f"--proxy-bypass-list={_bypass_domains}",
            "--new-window", "about:blank",
        ])
    except Exception:
        return False

    import time as _t
    for _ in range(10):
        _t.sleep(1)
        if _cdp_check_connection(debug_port):
            return True
    return False


def _cdp_check_connection(debug_port: int, timeout: int = 3) -> bool:
    try:
        from urllib.request import Request, urlopen
        req = Request(f"http://127.0.0.1:{debug_port}/json/version")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return "Browser" in data or "webSocketDebuggerUrl" in data
    except Exception:
        return False


def _cdp_list_tabs(debug_port: int) -> list:
    try:
        from urllib.request import Request, urlopen
        raw = urlopen(Request(f"http://127.0.0.1:{debug_port}/json/list"), timeout=10).read().decode()
        return json.loads(raw)
    except Exception:
        return []


def _cdp_open_page(debug_port: int, url: str) -> dict:
    from urllib.request import Request, urlopen
    raw = urlopen(
        Request(f"http://127.0.0.1:{debug_port}/json/new?{quote(url, safe=':/?&=%')}", method="PUT"),
        timeout=20,
    ).read().decode()
    return json.loads(raw)


def _cdp_close_page(debug_port: int, page_id: str):
    try:
        from urllib.request import Request, urlopen
        urlopen(Request(f"http://127.0.0.1:{debug_port}/json/close/{page_id}"), timeout=5)
    except Exception:
        pass


def _cdp_open_login_pages(debug_port: int, urls: list) -> int:
    """Open each URL as a new tab via CDP. Returns the number of tabs opened.

    Used by the Phase 2 login checkpoint: auto-launches a debug browser
    (see _cdp_ensure_browser), then pops open the publisher login pages
    so the user can sign in once per session. Cookies persist in the
    `runtime/debug_browser_profile` user-data-dir across runs, so after
    the first login the checkpoint becomes near-instant (user just
    verifies they're still signed in and clicks 继续).

    Fails gracefully: any individual tab-open error is swallowed so one
    bad URL does not break the whole checkpoint.
    """
    if not urls:
        return 0
    if not _cdp_check_connection(debug_port):
        return 0
    opened = 0
    for u in urls:
        if not u or not isinstance(u, str):
            continue
        try:
            _cdp_open_page(debug_port, u)
            opened += 1
        except Exception:
            # Individual failure (bad URL, transient socket hiccup) must
            # not prevent the remaining login tabs from opening.
            continue
    return opened


def _cdp_call(ws_url: str, method: str, params: dict = None, msg_id: int = 1, timeout: int = 180) -> dict:
    ws = _websocket_mod.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == msg_id:
                return msg
    finally:
        ws.close()


def _cdp_evaluate(ws_url: str, expression: str, await_promise: bool = False, msg_id: int = 1):
    msg = _cdp_call(ws_url, "Runtime.evaluate", {
        "expression": expression, "returnByValue": True, "awaitPromise": await_promise,
    }, msg_id=msg_id)
    return msg.get("result", {}).get("result", {}).get("value")


def _cdp_fetch_pdf_in_context(ws_url: str, pdf_url: str) -> Optional[bytes]:
    """Execute fetch() inside a page context to download a PDF. Returns bytes or None."""
    import base64
    _cdp_evaluate(ws_url, f'window.__pdfUrl = {json.dumps(pdf_url)};', msg_id=40)
    js = '''
fetch(window.__pdfUrl, {credentials: "include"})
  .then(r => { if (!r.ok) return "ERR:HTTP_" + r.status; return r.arrayBuffer(); })
  .then(buf => {
    if (typeof buf === "string") return buf;
    const bytes = new Uint8Array(buf);
    const chunk = 0x8000;
    let binary = "";
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  })
  .catch(e => "ERR:" + e.message)
'''
    try:
        value = _cdp_evaluate(ws_url, js.strip(), await_promise=True, msg_id=50)
        if not value or (isinstance(value, str) and value.startswith("ERR:")):
            return None
        data = base64.b64decode(value)
        return data if data[:5] == b"%PDF-" else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════
class PDFDownloader:
    """Smart multi-source PDF downloader with caching."""

    def __init__(self, cache_dir: Optional[Path] = None, email: Optional[str] = None,
                 scraper_api_keys: Optional[list] = None,
                 llm_api_key: str = "", llm_base_url: str = "", llm_model: str = "",
                 cdp_debug_port: int = 0,
                 disable_llm_search: bool = False,
                 s2_api_key: str = "",
                 core_api_key: str = ""):
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._email = email or "citationclaw@research.tool"
        self._scraper_keys = scraper_api_keys or []
        self._llm_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._llm_model = llm_model
        self._cdp_debug_port = cdp_debug_port
        self._llm_search_disabled = disable_llm_search  # True = skip LLM search entirely
        # 2026-04-21: circuit breaker for CDP-Elsevier Cloudflare stalls.
        # Observed run: 70 attempts, 0 successes, ~8 min wasted waiting
        # for manual Turnstile clicks that never came. Once we've burned
        # through _CDP_ELSEVIER_MAX_CF_TIMEOUTS consecutive Cloudflare
        # timeouts we assume the user isn't available / the challenge is
        # uncrackable this session and skip the tier to save time. Reset
        # on any successful CDP-Elsevier download.
        self._cdp_elsevier_cf_timeouts: int = 0
        self._cdp_elsevier_disabled: bool = False
        # 2026-04-21: ScienceDirect risk-control mitigation. User observed
        # that rapid tab switching trips SD's own rate limiter on top of
        # Cloudflare Turnstile. Three mitigations:
        #   1. `_elsevier_sem` -- serialize CDP-Elsevier attempts to
        #      concurrency=1. Prevents 5 workers navigating 5 SD tabs at
        #      once which SD reliably treats as bot behavior.
        #   2. Inter-request pacing: wait at least _ELSEVIER_MIN_GAP_S
        #      seconds between two CDP-Elsevier attempts so tab switches
        #      look human.
        #   3. Cooldown window after CF detection: once we hit a CF
        #      challenge, skip Elsevier entirely for _ELSEVIER_COOLDOWN_S
        #      so the IP can fall out of SD's bad-bot window. Unlike the
        #      circuit breaker above, cooldown is TEMPORARY -- the tier
        #      recovers after the window passes.
        # Lazy-init the semaphore on first use -- creating an asyncio
        # primitive in __init__ risks "attached to a different loop" if
        # the PDFDownloader is shared across loops.
        self._elsevier_sem = None  # type: ignore[assignment]
        self._elsevier_last_request_at: float = 0.0
        self._elsevier_cooldown_until: float = 0.0
        # S2 API key — drops rate-limit from 1 req/s to 100 req/s
        self._s2_api_key = s2_api_key or ""
        # CORE API key (free tier: 1000 req/day) — enables the CORE source
        self._core_api_key = core_api_key or ""
        # Memoize expensive GS "all versions" scrapes per URL — avoids 3× cost
        # when the download cascade retries.
        self._gs_versions_cache: dict = {}
        # Memoize S2 lookups per (s2_id, title) — cascade retries re-enter this
        self._s2_cache: dict = {}

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

    @staticmethod
    def _normalize_doi(doi: str) -> str:
        """Strip 'https://doi.org/' prefix and lowercase to stabilise cache key.

        S2 returns DOIs without the prefix; OpenAlex returns them with. Without
        normalisation the cache hash of the same paper differs between runs
        depending on which source populated the metadata this time, leading to
        spurious re-downloads (and inflated ScraperAPI cost).
        """
        if not doi:
            return ""
        d = doi.strip()
        for p in ("https://doi.org/", "http://doi.org/",
                  "https://dx.doi.org/", "http://dx.doi.org/"):
            if d.lower().startswith(p):
                d = d[len(p):]
                break
        return d.lower()

    def _cache_path(self, paper: dict) -> Path:
        norm_doi = self._normalize_doi(paper.get("doi") or "")
        key = (norm_doi or paper.get("Paper_Title")
               or paper.get("title") or "unknown")
        h = hashlib.md5(key.encode()).hexdigest()
        return self._cache_dir / f"{h}.pdf"

    # ── Core: try downloading a single URL ────────────────────────────
    async def _try_url(self, client, url: str, cookies: dict = None,
                       log=None, tag: str = "") -> Optional[bytes]:
        """Try downloading from a URL, handling HTML pages with PDF extraction.

        If ``log`` and ``tag`` are provided, explains *why* the URL failed
        (non-200 status, HTML without PDF link, publisher login wall, etc.).
        Without them the behaviour is unchanged (silent on failure).
        """
        def _dbg(msg: str):
            if log and tag:
                try:
                    log(f"    [{tag}] {msg}")
                except UnicodeEncodeError:
                    pass

        try:
            resp = await client.get(url, cookies=cookies or {})
            if resp.status_code != 200:
                _dbg(f"HTTP {resp.status_code}: {url[:80]}")
                return None
            if resp.content[:5] == b"%PDF-":
                return resp.content
            # HTML page → try extracting real PDF link
            if len(resp.content) > 100:
                html_text = resp.text
                pdf_url = _extract_pdf_url_from_html(html_text, str(resp.url))
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
                            _dbg(f"二级PDF链接也非PDF: {inner[:80]}")
                        else:
                            _dbg(f"PDF链接返回非PDF且无内嵌: {pdf_url[:80]}")
                    else:
                        _dbg(f"PDF链接 HTTP {resp2.status_code}: {pdf_url[:80]}")
                else:
                    # Classify the HTML: login page / paywall / generic
                    sniff = html_text[:3000].lower()
                    if any(s in sniff for s in ("institution/login", "seamlessaccess",
                                                "getaccess", "/purchase",
                                                "sign in", "登录", "captcha",
                                                "access denied", "forbidden")):
                        _dbg(f"登录/付费墙页面 (非PDF): {url[:80]}")
                    else:
                        _dbg(f"HTML页面无PDF链接: {url[:80]}")
            else:
                _dbg(f"响应过短 ({len(resp.content)}B): {url[:80]}")
        except Exception as e:
            _dbg(f"异常 {type(e).__name__}: {str(e)[:60]} @ {url[:60]}")
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
                        if (resp2.status_code == 200
                                and resp2.content[:5] == b"%PDF-"
                                and len(resp2.content) > 1000
                                and not _pdf_bytes_are_mojibake(resp2.content)):
                            await client.aclose()
                            return resp2.content
                await client.aclose()
                return None

            # Direct PDF response from rendered page?
            # Mojibake guard: render=true on a PDF endpoint returns text-corrupted
            # bytes. Reject those so the cascade can retry with render=false.
            if (resp.content[:5] == b"%PDF-" and len(resp.content) > 1000
                    and not _pdf_bytes_are_mojibake(resp.content)):
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
            # Use same session for cookie persistence (important for IEEE multi-hop).
            # All PDF-bytes returns guard against mojibake (render=true corrupts
            # binary responses when the proxy pipes them through a headless browser).
            pdf_scraper_url = self._scraper_build_url(pdf_link, publisher, session_num)
            if pdf_scraper_url:
                pdf_resp = await client.get(pdf_scraper_url)
                if pdf_resp.status_code == 200 and pdf_resp.content[:5] == b"%PDF-":
                    if (len(pdf_resp.content) > 1000
                            and not _pdf_bytes_are_mojibake(pdf_resp.content)):
                        await client.aclose()
                        return pdf_resp.content
                    # Got %PDF- bytes but too small OR mojibake. Rare but
                    # worth surfacing since the caller's _ok() won't see
                    # them (we return None below).
                    if log:
                        reason = ("太小" if len(pdf_resp.content) <= 1000
                                  else "mojibake损坏")
                        log(f"    [ScraperAPI] {publisher.upper()} PDF拿到但{reason}"
                            f" ({len(pdf_resp.content)}B)")
                else:
                    # 2026-04-21: was silent — hid 99% of Elsevier CF
                    # failures. Log the HTTP status so users see why the
                    # download after the "PDF链接" log line didn't
                    # produce a [PDF OK]. Snippet first 60 bytes of body
                    # so Cloudflare challenge HTML is obvious
                    # ('<!DOCTYPE html>...Just a moment').
                    if log:
                        body_snip = pdf_resp.content[:60].decode(
                            "utf-8", "replace").replace("\n", " ")
                        # Classify the HTML body so the trace reads as
                        # an actual DIAGNOSIS, not raw bytes the user
                        # has to decode by eye.
                        body_lower = pdf_resp.content[:4000].decode(
                            "utf-8", "replace").lower()
                        if ("just a moment" in body_lower
                                or "challenge-platform" in body_lower
                                or "checking your browser" in body_lower):
                            tag = "Cloudflare 挑战页 (Turnstile)"
                        elif "access denied" in body_lower:
                            tag = "Akamai/generic 访问拒绝"
                        elif "sciencedirect" in body_lower and "pdf" in body_lower:
                            tag = ("Elsevier 查看器壳 (未认证不能直接下载 PDF bytes;"
                                   " 需要 CDP 通道或机构 cookie)")
                        elif "springer" in body_lower or "link.springer.com" in body_lower:
                            tag = ("Springer 查看器壳 (同 Elsevier，"
                                   "ScraperAPI residential 不够)")
                        elif pdf_resp.status_code == 200:
                            tag = "HTTP 200 但非 PDF 字节"
                        else:
                            tag = f"HTTP {pdf_resp.status_code}"
                        log(f"    [ScraperAPI] {publisher.upper()} PDF 下载"
                            f"失败: {tag} | {body_snip!r}")

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
                                    and len(inner_resp.content) > 1000
                                    and not _pdf_bytes_are_mojibake(inner_resp.content)):
                                await client.aclose()
                                return inner_resp.content

            # ── Step 5: Try direct download (some PDF URLs are public) ──
            direct_status = None
            try:
                direct_resp = await client.get(pdf_link)
                direct_status = direct_resp.status_code
                if (direct_resp.status_code == 200
                        and direct_resp.content[:5] == b"%PDF-"
                        and len(direct_resp.content) > 1000
                        and not _pdf_bytes_are_mojibake(direct_resp.content)):
                    await client.aclose()
                    return direct_resp.content
            except Exception as e:
                direct_status = f"{type(e).__name__}: {str(e)[:60]}"

            # 2026-04-21: final explicit log so the trace doesn't look
            # like "PDF链接: ..." then radio silence.
            if log:
                log(f"    [ScraperAPI] {publisher.upper()} 直连PDF也失败"
                    f" (status={direct_status})")

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

    # ── Minimal ScraperAPI proxy fetch (no JS render, no link extraction) ──
    async def _scraper_fetch_url(self, url: str) -> Optional[bytes]:
        """Fetch a URL through ScraperAPI with PDF-friendly defaults.

        Use this when we already know the target URL (e.g. returned by V-API
        search) but the direct fetch failed due to IP blocks / region gating.
        ScraperAPI rotates through residential IPs on `premium=true`.

        Policy mirrors `_smart_scraper_download`:
          - `.pdf` / `/pdf/` / `pdfft` URLs -> `render=false` (avoid
            headless-browser binary mojibake).
          - Other URLs -> `render=true` (lets JS-gated preprint servers show
            the PDF endpoint).
          - On mojibake detection -> single retry with `render=false`.
        """
        if not self._scraper_keys:
            return None
        key = self._scraper_keys[0]
        lower = url.lower()
        pdf_like = (lower.endswith(".pdf") or "/pdf/" in lower
                    or "pdfft" in lower or "citation_pdf_url" in lower)

        def _build(render: bool) -> str:
            parts = [f"api_key={key}", f"url={quote(url)}",
                     f"render={'true' if render else 'false'}",
                     "premium=true", "country_code=us"]
            return "https://api.scraperapi.com?" + "&".join(parts)

        try:
            from citationclaw.core.http_utils import make_async_client
            client = make_async_client(timeout=60.0)
            resp = await client.get(_build(render=not pdf_like))
            if resp.status_code == 200 and resp.content[:5] == b"%PDF-":
                if not _pdf_bytes_are_mojibake(resp.content):
                    await client.aclose()
                    return resp.content
                # Mojibake -> retry without render
                resp2 = await client.get(_build(render=False))
                await client.aclose()
                if (resp2.status_code == 200
                        and resp2.content[:5] == b"%PDF-"
                        and not _pdf_bytes_are_mojibake(resp2.content)):
                    return resp2.content
                return None
            await client.aclose()
        except Exception:
            pass
        return None

    # ── ScraperAPI + LLM smart fallback (for stubborn publisher pages) ──
    async def _smart_scraper_download(self, url: str) -> Optional[bytes]:
        """Last-resort: use ScraperAPI to render publisher page, then find PDF link.

        ScraperAPI renders JavaScript, bypasses Cloudflare, handles cookies.
        If direct extraction fails, uses lightweight LLM to analyze the HTML.

        Mojibake note (2026-04-20): ScraperAPI's `render=true` pipes the
        target response through a headless browser. When the target URL
        returns a PDF, the browser treats the raw PDF bytes as text and
        re-encodes them as UTF-8, corrupting every 0x80+ byte into either
        `\\xc3\\xXX` (soft) or `\\xef\\xbf\\xbd` (hard). The returned bytes
        still start with `%PDF-` and open in PyMuPDF, but content streams
        fail zlib decode -> empty page text. We therefore:
          - try render=false first when the URL looks like a direct PDF,
          - reject any PDF response that triggers `_pdf_bytes_are_mojibake`,
          - on rejection, refetch with render=false.
        """
        if not self._scraper_keys:
            return None

        key = self._scraper_keys[0]

        def _build(u: str, render: bool, premium: bool = True) -> str:
            parts = [f"api_key={key}", f"url={quote(u)}"]
            parts.append(f"render={'true' if render else 'false'}")
            if premium:
                parts.append("premium=true")
            parts.append("country_code=us")
            return "https://api.scraperapi.com?" + "&".join(parts)

        # If the URL already looks like a direct PDF endpoint, skip the
        # JS renderer on the first hop — render=true on a binary response
        # mojibakes the bytes (see docstring).
        lower_url = url.lower()
        pdf_like = lower_url.endswith(".pdf") or "/pdf/" in lower_url or \
                   "citation_pdf_url" in lower_url or "pdfft" in lower_url

        try:
            from citationclaw.core.http_utils import make_async_client
            client = make_async_client(timeout=60.0)

            # Hop 1: fetch the target. Render only when HTML is expected.
            first_url = _build(url, render=not pdf_like)
            resp = await client.get(first_url)
            if resp.status_code != 200:
                await client.aclose()
                return None

            # Direct PDF?
            if resp.content[:5] == b"%PDF-":
                if not _pdf_bytes_are_mojibake(resp.content):
                    await client.aclose()
                    return resp.content
                # Mojibake: retry without render=true to get raw bytes.
                raw_url = _build(url, render=False)
                resp_raw = await client.get(raw_url)
                await client.aclose()
                if (resp_raw.status_code == 200
                        and resp_raw.content[:5] == b"%PDF-"
                        and not _pdf_bytes_are_mojibake(resp_raw.content)):
                    return resp_raw.content
                return None

            # If we intentionally skipped rendering and got HTML back,
            # re-fetch with rendering to run the JS and expose the PDF link.
            if pdf_like:
                render_url = _build(url, render=True)
                resp = await client.get(render_url)
                if resp.status_code != 200:
                    await client.aclose()
                    return None
                if resp.content[:5] == b"%PDF-":
                    if not _pdf_bytes_are_mojibake(resp.content):
                        await client.aclose()
                        return resp.content
                    # fall through to link extraction

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

            # Download the found PDF link WITHOUT render=true (avoid mojibake).
            pdf_scraper_url = _build(pdf_link, render=False)
            pdf_resp = await client.get(pdf_scraper_url)
            if (pdf_resp.status_code == 200
                    and pdf_resp.content[:5] == b"%PDF-"
                    and not _pdf_bytes_are_mojibake(pdf_resp.content)):
                await client.aclose()
                return pdf_resp.content

            # Try direct download (some PDF links don't need ScraperAPI)
            cookies = _get_cookies_for_url(pdf_link)
            pdf_resp2 = await client.get(pdf_link, cookies=cookies)
            await client.aclose()
            if (pdf_resp2.status_code == 200
                    and pdf_resp2.content[:5] == b"%PDF-"
                    and not _pdf_bytes_are_mojibake(pdf_resp2.content)):
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
                max_retries=0,  # we own the retry policy; don't let SDK compound timeouts
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
        if not self._llm_key or self._llm_search_disabled:
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

            # Use user's configured model directly — don't override.
            # Most modern LLMs (Gemini, GPT, DeepSeek) can suggest arXiv/repo
            # URLs from training knowledge even without explicit search grounding.
            # Overriding to a search model causes 401 when user's plan doesn't
            # include it, and the configured model shares the same API key.
            search_model = self._llm_model

            if log:
                log(f"    [LLM搜索] 搜索替代PDF: {title[:50]}...")

            # Search-grounded models need longer timeout (they search the web).
            # 2026-04-20: OpenAI SDK defaults to max_retries=2 which on a
            # 90s-hanging upstream compounds into 270s+ delays per paper.
            # We disable SDK retries (our own 429-retry loop below owns the
            # policy) and keep a 90s per-attempt timeout — the observed
            # successful search-grounded latencies span 20-60s, so 90s gives
            # enough headroom without letting any single attempt stall the
            # whole pipeline.
            import httpx as _httpx
            http_client = _httpx.AsyncClient(timeout=90.0, trust_env=True)
            client = AsyncOpenAI(
                api_key=self._llm_key,
                base_url=self._llm_base_url.rstrip("/") + "/" if self._llm_base_url else None,
                http_client=http_client,
                max_retries=0,
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

            # Retry loop for transient upstream saturation.
            #
            # 2026-04-20: V-API's gpt.ge frequently answers 429 with
            # `upstream_error` + "负载已饱和" (upstream Gemini capacity,
            # NOT our plan's rate limit). Old behaviour treated this as
            # terminal and disabled LLM search for the whole harness run
            # after just one transient miss. Now: 2 retries at 5s/15s;
            # only a *persistent* 429 disables the run.
            last_err = None
            resp = None
            for attempt, backoff in enumerate([0, 5, 15]):
                if backoff:
                    await asyncio.sleep(backoff)
                try:
                    resp = await client.chat.completions.create(
                        model=search_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                    )
                    break
                except Exception as e:
                    last_err = e
                    err = str(e)
                    # Only retry on 429 / upstream saturation; fail fast on
                    # 401 / 403 (auth or billing) since retries won't help.
                    if "429" not in err and "upstream_error" not in err:
                        raise
                    if log and attempt < 2:
                        log(f"    [LLM搜索] 上游 429，{[5,15][attempt]}s 后重试 ({attempt+1}/2)")
            if resp is None:
                # All retries exhausted on 429. Raise to outer handler
                # which decides whether to disable the run.
                raise last_err if last_err else RuntimeError("LLM search failed")

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

            # Try downloading each candidate (with title verification).
            # 2026-04-20: also fall back to ScraperAPI for URLs that return
            # non-200 directly — catches the case where the LLM suggests a
            # ResearchGate / institutional repo URL that blocks datacenter
            # IPs but opens fine through a residential proxy.
            dl_client = self._make_client(timeout=30.0)
            async with dl_client as c:
                for i, url in enumerate(urls[:5]):  # Try top 5
                    # Direct try
                    try:
                        if log:
                            log(f"    [LLM搜索] 尝试 ({i+1}): {url[:70]}...")
                        data = await self._try_url(c, url)
                        if data and len(data) > 1000 and data[:5] == b"%PDF-":
                            # Verify this is actually the right paper before returning.
                            # Without this check, LLM hallucinated URLs (e.g. wrong
                            # OpenReview ID) would be accepted as "success" and the
                            # remaining candidate URLs would never be tried.
                            if (not _pdf_bytes_are_mojibake(data)
                                    and title and len(title) > 10
                                    and not _pdf_title_matches(data, title)):
                                if log:
                                    log(f"    [LLM搜索] ({i+1}) 标题不匹配，跳过")
                                continue
                            if _pdf_bytes_are_mojibake(data):
                                # shouldn't happen on direct fetch, but guard anyway
                                continue
                            if log:
                                log(f"    [LLM搜索] 下载成功: {len(data)//1024}KB")
                            return data
                    except Exception:
                        pass

                    # ScraperAPI rescue: if direct fetch didn't yield a clean
                    # PDF, proxy the same URL via ScraperAPI. Skips render=true
                    # on PDF-looking URLs (same mojibake-avoidance policy as
                    # `_smart_scraper_download`).
                    if self._scraper_keys:
                        try:
                            data = await self._scraper_fetch_url(url)
                            if (data and len(data) > 1000 and data[:5] == b"%PDF-"
                                    and not _pdf_bytes_are_mojibake(data)):
                                if (title and len(title) > 10
                                        and not _pdf_title_matches(data, title)):
                                    if log:
                                        log(f"    [LLM搜索] ({i+1}) 代理后标题不匹配，跳过")
                                    continue
                                if log:
                                    log(f"    [LLM搜索] 代理下载成功: {len(data)//1024}KB")
                                return data
                        except Exception:
                            pass

            if log:
                log(f"    [LLM搜索] 所有候选URL均失败")
            return None

        except Exception as e:
            err_str = str(e)
            err_type = type(e).__name__
            lower = err_str.lower()
            # Differentiate failure classes (2026-04-20 / 2026-04-21):
            #   - auth/billing (401 / 403 / "insufficient")  -> disable run
            #   - upstream 429 after retries                  -> count misses,
            #     disable only after 3 consecutive across the run
            #   - other errors                                 -> log + continue
            is_auth = ("401" in err_str or "403" in err_str
                       or "insufficient" in lower or "invalid_api_key" in lower
                       or "unauthori" in lower)
            is_429 = ("429" in err_str or "upstream_error" in err_str
                      or "负载" in err_str or "saturat" in lower)
            if is_auth:
                self._llm_search_disabled = True
                if log:
                    log(f"    [LLM搜索] 认证/计费失败，本次运行已禁用: "
                        f"{err_type}: {err_str[:80]}")
            elif is_429:
                # Persistent-saturation circuit breaker.
                self._llm_search_429_misses = getattr(
                    self, "_llm_search_429_misses", 0
                ) + 1
                if log:
                    log(f"    [LLM搜索] 上游 429 持续，跳过本篇 "
                        f"(累计 {self._llm_search_429_misses}/3)")
                if self._llm_search_429_misses >= 3:
                    self._llm_search_disabled = True
                    if log:
                        log(f"    [LLM搜索] 3 次上游 429，本次运行已禁用 LLM 搜索")
            else:
                # 2026-04-21: upgraded from `{err_str[:80]}` to include
                # the exception CLASS name. Previously the log just said
                # `异常: Connection error.` which gave no diagnostic hint
                # (timeout? TLS? DNS? proxy? upstream 502?). Surfacing
                # the type (e.g. `APIConnectionError`, `ReadTimeout`,
                # `ConnectError`) makes it grep-able and lets the user
                # tell whether it's us (network config) or gpt.ge (up-
                # stream). Also bumped to 140 chars to keep useful tails
                # like the request id.
                if log:
                    log(f"    [LLM搜索] 异常: {err_type}: {err_str[:140]}")
            return None

    # ── CDP: IEEE Xplore ────────────────────────────────────────────────
    async def _try_cdp_ieee(self, paper: dict, log=None) -> Optional[bytes]:
        """Download IEEE paper via CDP browser session.

        Reuses an existing authenticated IEEE tab, or opens stamp.jsp and
        waits for user to complete authentication if needed.
        Uses in-page fetch() to download getPDF.jsp with session cookies.
        """
        if not _cdp_ensure_browser(self._cdp_debug_port):
            return None

        link = paper.get("paper_link", "")
        m = re.search(r'/document/(\d+)', link)
        if not m:
            m = re.search(r'arnumber=(\d+)', link)
        if not m:
            return None
        arnumber = m.group(1)

        port = self._cdp_debug_port
        get_pdf_url = f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}&ref="

        def _sync():
            import time as _t

            # Strategy 1: reuse existing IEEE tab
            for t in _cdp_list_tabs(port):
                if t.get("type") == "page" and "ieeexplore.ieee.org" in t.get("url", ""):
                    ws = t.get("webSocketDebuggerUrl", "")
                    if ws:
                        data = _cdp_fetch_pdf_in_context(ws, get_pdf_url)
                        if data:
                            return data
                    break

            # Strategy 2: open stamp page, handle auth
            stamp_url = f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arnumber}"
            page = _cdp_open_page(port, stamp_url)
            ws_url = page.get("webSocketDebuggerUrl", "")
            if not ws_url:
                return None

            _t.sleep(8)

            current = _cdp_evaluate(ws_url, "window.location.href", msg_id=5)
            if current and "login" in str(current).lower():
                if log:
                    log("  [CDP-IEEE] 需要登录 — 请在浏览器窗口完成认证")
                deadline = _t.time() + 120
                while _t.time() < deadline:
                    _t.sleep(3)
                    try:
                        url_now = _cdp_evaluate(ws_url, "window.location.href", msg_id=50)
                    except Exception:
                        _t.sleep(2)
                        for tab in _cdp_list_tabs(port):
                            if (tab.get("type") == "page"
                                    and "ieeexplore.ieee.org" in tab.get("url", "")
                                    and "login" not in tab.get("url", "").lower()):
                                ws_url = tab.get("webSocketDebuggerUrl", ws_url)
                                url_now = tab.get("url", "")
                                break
                        else:
                            continue
                    if url_now and "login" not in str(url_now).lower() and "ieeexplore" in str(url_now).lower():
                        break
                else:
                    return None

                _cdp_call(ws_url, "Page.navigate", {"url": stamp_url}, msg_id=6)
                _t.sleep(8)

            data = _cdp_fetch_pdf_in_context(ws_url, get_pdf_url)
            try:
                _cdp_call(ws_url, "Page.navigate", {"url": "about:blank"}, msg_id=99)
            except Exception:
                pass
            return data

        try:
            return await asyncio.to_thread(_sync)
        except Exception:
            return None

    # ── CDP: Elsevier / ScienceDirect ─────────────────────────────────
    async def _try_cdp_elsevier(self, paper: dict, log=None) -> Optional[bytes]:
        """Download Elsevier paper via CDP browser session.

        Opens article page, extracts pdfDownload metadata from rendered HTML,
        navigates to pdfft URL. User passes Cloudflare Turnstile if prompted.
        Extracts PDF via Edge/Chrome PDF viewer or in-page fetch().

        Circuit breaker (2026-04-21): if this method has timed out on
        Cloudflare N consecutive times within the same run, subsequent
        invocations short-circuit to None without waiting. Resets on
        any successful download.
        """
        # Circuit-breaker short-circuit
        if self._cdp_elsevier_disabled:
            if log:
                log("  [CDP-Elsevier] 跳过: 电路断路器已熔断 "
                    f"(本次 run 连续 {self._cdp_elsevier_cf_timeouts} 次 "
                    "Cloudflare 超时)")
            return None

        # ScienceDirect cooldown window (2026-04-21). Triggered when a
        # previous attempt saw a CF challenge; gives SD's risk-control
        # window a chance to forget our IP before we hit them again.
        loop = asyncio.get_event_loop()
        now = loop.time()
        if now < self._elsevier_cooldown_until:
            remaining = int(self._elsevier_cooldown_until - now)
            if log:
                log(f"  [CDP-Elsevier] 跳过: SD 冷却中 (还需 {remaining}s; "
                    f"上次 CF 检测触发 {self._ELSEVIER_COOLDOWN_S}s 冷却)")
            return None

        if not _cdp_ensure_browser(self._cdp_debug_port):
            return None

        link = paper.get("paper_link", "")
        m = re.search(r'/pii/([A-Z0-9]+)', link)
        if not m:
            return None
        target_pii = m.group(1)

        # Lazy-init the semaphore on the current loop.
        if self._elsevier_sem is None:
            self._elsevier_sem = asyncio.Semaphore(1)

        port = self._cdp_debug_port
        article_url = link or f"https://www.sciencedirect.com/science/article/pii/{target_pii}"

        # 2026-04-21: track whether _sync hit a Cloudflare challenge
        # during this invocation (set by the inner wait loops). Used by
        # the async wrapper to bump the CF-timeout circuit-breaker
        # counter.
        _hit_cf_box = {"saw": False}

        def _sync():
            import time as _t

            # Get or create a ScienceDirect tab
            ws_url = None
            for t in _cdp_list_tabs(port):
                if t.get("type") == "page" and "sciencedirect.com" in t.get("url", ""):
                    ws_url = t.get("webSocketDebuggerUrl", "")
                    break

            if ws_url:
                try:
                    _cdp_call(ws_url, "Page.navigate", {"url": article_url}, msg_id=1)
                except Exception:
                    ws_url = None

            if not ws_url:
                page = _cdp_open_page(port, article_url)
                ws_url = page.get("webSocketDebuggerUrl", "")
                if not ws_url:
                    return None

            _t.sleep(10)

            # Extract pdfDownload metadata (with Cloudflare retry, up to 60s)
            pdfft_url = None
            deadline_meta = _t.time() + 60
            attempt = 0
            while _t.time() < deadline_meta:
                attempt += 1
                html = _cdp_evaluate(ws_url, "document.documentElement.outerHTML", msg_id=10)
                if not html:
                    _t.sleep(3)
                    continue

                # Cloudflare challenge page?
                if "challenge-platform" in html or "Just a moment" in html or len(html) < 5000:
                    _hit_cf_box["saw"] = True
                    if log and attempt <= 3:
                        log("  [CDP-Elsevier] Cloudflare 验证 — 请在浏览器中完成验证")
                    _t.sleep(5)
                    continue

                mm = _SD_PDF_DOWNLOAD_RE.search(html)
                if not mm:
                    if _t.time() + 10 < deadline_meta:
                        _t.sleep(5)
                        continue
                    return None

                md5, pid, found_pii, ext, path = mm.groups()
                if found_pii != target_pii:
                    _cdp_call(ws_url, "Page.navigate", {"url": article_url}, msg_id=11)
                    _t.sleep(10)
                    continue

                pdfft_url = f"https://www.sciencedirect.com/{path}/{found_pii}{ext}?md5={md5}&pid={pid}"
                break

            if not pdfft_url:
                return None

            # Navigate to pdfft (may trigger Cloudflare Turnstile)
            if log:
                log("  [CDP-Elsevier] 导航到 PDF 下载页")
            _cdp_call(ws_url, "Page.navigate", {"url": pdfft_url}, msg_id=15)

            # Wait for PDF viewer to appear (up to 120s)
            deadline_pdf = _t.time() + 120
            last_msg = 0
            while _t.time() < deadline_pdf:
                _t.sleep(3)
                viewer = None
                pdf_page = None
                for t in _cdp_list_tabs(port):
                    # Edge PDF viewer
                    if t.get("type") == "webview" and "edge_pdf" in t.get("url", ""):
                        viewer = t
                    # Chrome/Edge tab with PDF content
                    if t.get("type") == "page" and "pdf.sciencedirectassets.com" in t.get("url", ""):
                        pdf_page = t

                if viewer and pdf_page:
                    try:
                        orig_url = _cdp_evaluate(
                            viewer["webSocketDebuggerUrl"],
                            'document.querySelector("embed").getAttribute("original-url")',
                            msg_id=30,
                        )
                        if orig_url and "pdf" in orig_url.lower():
                            if (target_pii.upper() in orig_url.upper()
                                    or target_pii.upper() in pdf_page.get("url", "").upper()):
                                data = _cdp_fetch_pdf_in_context(pdf_page["webSocketDebuggerUrl"], orig_url)
                                if data:
                                    return data
                    except Exception:
                        pass

                # Fallback: try fetching pdfft directly in page context
                if pdf_page:
                    try:
                        data = _cdp_fetch_pdf_in_context(pdf_page["webSocketDebuggerUrl"], pdfft_url)
                        if data:
                            return data
                    except Exception:
                        pass

                now = _t.time()
                if log and now - last_msg > 15:
                    log(f"  [CDP-Elsevier] 等待 PDF... ({int(deadline_pdf - now)}s)")
                    last_msg = now

            # 2026-04-21: PDF viewer never showed up after 120s. On
            # ScienceDirect this almost always means CF is holding the
            # pdfft URL hostage (Turnstile not solved). Mark as CF so
            # the outer wrapper triggers cooldown + counts toward the
            # circuit breaker.
            _hit_cf_box["saw"] = True
            return None

        # Serialize + pace CDP-Elsevier operations (2026-04-21).
        # Acquiring the semaphore means only ONE worker is talking to
        # ScienceDirect at a time. The min-gap enforcement means even
        # if one worker finishes quickly, the next worker waits
        # `_ELSEVIER_MIN_GAP_S` seconds before starting its tab
        # navigation. This addresses SD's risk-control mechanism that
        # flags rapid same-IP tab switches as bot behavior.
        async with self._elsevier_sem:
            now = loop.time()
            gap = now - self._elsevier_last_request_at
            if 0 < gap < self._ELSEVIER_MIN_GAP_S:
                wait = self._ELSEVIER_MIN_GAP_S - gap
                if log:
                    log(f"  [CDP-Elsevier] SD 降速: 与上次请求间隔 "
                        f"{gap:.1f}s < {self._ELSEVIER_MIN_GAP_S}s, "
                        f"等待 {wait:.1f}s")
                await asyncio.sleep(wait)
            self._elsevier_last_request_at = loop.time()

            try:
                result = await asyncio.to_thread(_sync)
            except Exception:
                result = None

        # Circuit-breaker bookkeeping (2026-04-21).
        if result:
            # Success -> reset the counter so a later transient stall
            # doesn't permanently disable the tier.
            self._cdp_elsevier_cf_timeouts = 0
        elif _hit_cf_box["saw"]:
            # We tried, we saw CF, we gave up. Count it + trigger
            # cooldown so next worker doesn't immediately try SD again.
            self._cdp_elsevier_cf_timeouts += 1
            self._elsevier_cooldown_until = (
                loop.time() + self._ELSEVIER_COOLDOWN_S
            )
            if (self._cdp_elsevier_cf_timeouts
                    >= self._CDP_ELSEVIER_MAX_CF_TIMEOUTS):
                self._cdp_elsevier_disabled = True
                if log:
                    log(f"  [CDP-Elsevier] 连续 "
                        f"{self._cdp_elsevier_cf_timeouts} 次 Cloudflare "
                        f"超时，本次 run 自动禁用 CDP-Elsevier 通道 "
                        f"（节省后续 Elsevier paper 各 120s 空等）。"
                        f"下次启动 server 会自动重置。")
            else:
                if log:
                    log(f"  [CDP-Elsevier] 本篇超时 "
                        f"(CF 计数 {self._cdp_elsevier_cf_timeouts}/"
                        f"{self._CDP_ELSEVIER_MAX_CF_TIMEOUTS}); "
                        f"SD 冷却 {self._ELSEVIER_COOLDOWN_S}s 期间后续 "
                        f"Elsevier paper 跳过 CDP 通道")
        return result

    # ── Main download method (PaperRadar-style smart download) ────────
    _RETRY_ATTEMPTS = 2      # total attempts = 1 + retries
    _RETRY_DELAY = 8         # seconds between retries
    # CDP-Elsevier Cloudflare Turnstile circuit breaker (2026-04-21).
    # After this many consecutive attempts that hit a CF challenge AND
    # time out without resolution, disable CDP-Elsevier for the rest of
    # the run. The alternative is waiting 120s per Elsevier paper in
    # a 100-paper batch -- that's an hour of dead time.
    _CDP_ELSEVIER_MAX_CF_TIMEOUTS = 3
    # ScienceDirect pacing (2026-04-21). SD's own rate limiter flags
    # rapid-fire navigation from the same IP/session as bot behavior,
    # on TOP of Cloudflare Turnstile. These two constants serialize and
    # pace CDP-Elsevier attempts so the traffic looks less bot-like.
    _ELSEVIER_MIN_GAP_S = 15   # minimum seconds between consecutive attempts
    _ELSEVIER_COOLDOWN_S = 300 # after CF hit, skip SD for 5 minutes
    # On terminal failure, how many of the cascade's own log lines to
    # replay as part of the diagnostic block. Observed 2026-04-21: a
    # typical Taylor & Francis failure produces 44 lines (GS版本页 tier
    # alone retries 3-4 URLs per attempt × 3 attempts); a cap of 40 was
    # truncating the head of the trace. 60 covers the full ~15-tier
    # cascade × 3 attempts with margin, while keeping the block under
    # ~70 lines in run.log (still greppable).
    _FAIL_TRACE_MAX_LINES = 60

    def _cache_is_valid(self, cached: Path, full_title: str) -> bool:
        """Return True iff the cached PDF passes a title match against the
        expected paper title. Corrupt, zero-size, and wrong-paper caches (left
        over from an older, less strict verifier) are considered invalid.

        Also rejects mojibake-corrupted caches caused by older code paths
        that wrote `response.text.encode("utf-8")` (or similar text-round-trip)
        instead of `response.content`:

          (a) **Hard corruption**: bytes that failed UTF-8 decode became
              U+FFFD (\\xef\\xbf\\xbd). 3+ consecutive U+FFFD near the header
              is a strong signature -- no legitimate PDF has it.
          (b) **Soft corruption**: bytes already valid as UTF-8 passed through
              a Latin-1 decode + UTF-8 re-encode, doubling every high-bit
              byte into a \\xc3\\xXX pair. The %PDF binary marker line
              (normally 4 raw high-bit bytes) becomes ~8 bytes with \\xc3 in
              every other slot. These open in PyMuPDF but content streams
              fail zlib decode (gibberish pages).
        """
        if not (cached.exists() and cached.stat().st_size > 0):
            return False
        try:
            data = cached.read_bytes()
        except Exception:
            return False
        if len(data) < 1000 or data[:5] != b"%PDF-":
            return False
        if _pdf_bytes_are_mojibake(data):
            return False
        if not full_title:
            return True  # no title to verify against — trust the header check
        return _pdf_title_matches(data, full_title)

    async def download(self, paper: dict, log=None, log_error=None,
                       log_ok=None) -> Optional[Path]:
        """Smart multi-source PDF download with automatic retry.

        On first failure, waits and retries the full cascade once.
        Transient errors (rate limits, timeouts, mirror flakiness) often
        resolve on the second attempt.

        Args:
            paper: paper dict with doi / pdf_url / paper_link / etc.
            log: callable(str) for per-tier diagnostic lines (INFO level
                 when wired to LogManager). Called ~5-30x per paper.
            log_error: optional callable(str) for the terminal
                       'all sources failed' block ONLY. Wire this to
                       LogManager.error to surface failures in red on
                       the UI log panel. Falls back to `log` if None.
            log_ok: optional callable(str) for SUCCESS-level messages
                    (cache hit, [PDF OK] on successful download). Wire
                    this to LogManager.success so the UI paints it
                    green. Falls back to `log` if None (backward compat
                    with 2026-04-20 behavior). Added 2026-04-21 per
                    user request: "成功了一篇文章后可以用绿色的文字
                    显示一下".

        On terminal failure this method emits a DIAGNOSTIC BLOCK via
        `log_error` containing:
          - the paper title + DOI + detected publisher
          - every log line the cascade emitted during its 3 attempts
            (last `_FAIL_TRACE_MAX_LINES` lines, to keep the block
            greppable without flooding run.log)
        """
        title = paper.get("Paper_Title", paper.get("title", "?"))[:40]
        full_title = paper.get("Paper_Title") or paper.get("title") or ""
        # Prefer log_ok for the cache-hit "success" message. Falls back
        # to log if log_ok not provided (preserves old callers).
        _emit_ok = log_ok if log_ok else log
        cached = self._cache_path(paper)
        if self._cache_is_valid(cached, full_title):
            if _emit_ok:
                _emit_ok(f"    [PDF缓存] {title}")
            return cached
        # Stale / wrong-paper cache — delete so redownload can overwrite it.
        if cached.exists() and cached.stat().st_size > 0:
            try:
                cached.unlink()
                if log:
                    log(f"    [PDF缓存] 已失效(标题不匹配), 重新下载: {title}")
            except OSError:
                pass

        # Per-paper cascade trace: tees every `log(...)` call from this
        # download's attempts into a local list. On success we throw it
        # away; on terminal failure we dump it as a diagnostic block.
        trace: list = []

        def _tee_log(msg: str):
            # Strip ANSI / leading whitespace for compact storage but keep
            # the line intact for live streaming.
            trace.append(msg)
            if log:
                try:
                    log(msg)
                except Exception:
                    pass  # A broken log sink must not break the download

        for attempt in range(1 + self._RETRY_ATTEMPTS):
            # log_ok threading: the [PDF OK] message inside _ok() is
            # emitted AT SUCCESS LEVEL when log_ok is provided, so the
            # UI paints it green. All other cascade chatter stays on the
            # _tee_log (INFO level) path.
            result = await self._download_once(
                paper, log=_tee_log, log_ok=log_ok,
            )
            if result:
                return result
            if attempt < self._RETRY_ATTEMPTS:
                _tee_log(f"    [PDF重试] {self._RETRY_DELAY}s 后重试 "
                         f"({attempt+1}/{self._RETRY_ATTEMPTS}): {title}")
                await asyncio.sleep(self._RETRY_DELAY)

        # Build the diagnostic block. Use `log_error` if available
        # (surfaces in red on UI, greppable as [ERROR]) otherwise fall
        # back to `log` so old callers keep working unchanged.
        emit = log_error if log_error else log
        if emit is None:
            return None  # no logger provided; silently return None

        doi = (paper.get("doi") or "").strip()
        paper_link = paper.get("paper_link") or paper.get("pdf_url") or ""
        pub = _detect_publisher(paper_link) if paper_link else "unknown"
        if pub == "unknown" and doi:
            pub = _publisher_from_doi(doi)

        header = (
            f"[PDF失败] {title}"
            + (f" | DOI={doi}" if doi else "")
            + (f" | pub={pub}" if pub != "unknown" else "")
        )
        emit(header)
        emit(
            f"  (cascade + {self._RETRY_ATTEMPTS} 次重试均未命中；"
            f"共 {len(trace)} 条尝试记录如下，最多显示最后 "
            f"{self._FAIL_TRACE_MAX_LINES} 条)"
        )
        tail = trace[-self._FAIL_TRACE_MAX_LINES:]
        for line in tail:
            # Strip the leading 4-space indent that cascade lines
            # already carry so our summary's own indent reads clean.
            emit(f"    >> {line.lstrip()}")
        emit(f"  [PDF失败] ^^ 上述 trace 属于: {title}")
        return None

    async def _download_once(self, paper: dict, log=None,
                             log_ok=None) -> Optional[Path]:
        """Single attempt: try all sources in cascade order.

        Args:
            log: callable for INFO-level cascade chatter.
            log_ok: optional callable for the SUCCESS-level [PDF OK]
                    message when a tier finally lands a valid PDF. If
                    not provided, falls back to `log`.
        """
        title = paper.get("Paper_Title", paper.get("title", "?"))[:40]
        full_title = paper.get("Paper_Title") or paper.get("title") or ""
        cached = self._cache_path(paper)
        if self._cache_is_valid(cached, full_title):
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
        # 2026-04-21: also extract arXiv ID from 10.48550/arXiv.<id>
        # DOIs. Observed PoolNet+ with DOI=10.48550/arxiv.2512.05362
        # failing because no tier recognized that prefix as arXiv.
        if not arxiv_id and doi:
            arxiv_from_doi = _arxiv_id_from_doi(doi)
            if arxiv_from_doi:
                arxiv_id = arxiv_from_doi
                if log:
                    log(f"    [arXiv] 从 DOI 解析 arxiv_id={arxiv_id}")
        paper_link = paper.get("paper_link") or ""
        gs_pdf_link = paper.get("gs_pdf_link") or ""
        gs_all_versions = paper.get("gs_all_versions") or ""
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
            # ── Mojibake guard: reject PDFs with text-round-trip corruption
            # (upstream `.text.encode("utf-8")` instead of `.content`).
            if _pdf_bytes_are_mojibake(data):
                if log:
                    try:
                        log(f"    [PDF SKIP] {_SOURCE_LABELS.get(source, source)} PDF二进制被文本往返损坏(mojibake)，跳过: {title}")
                    except UnicodeEncodeError:
                        pass
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
            # 2026-04-21: route [PDF OK] through log_ok (SUCCESS level)
            # when available so the UI paints it green and users can
            # set config.log_min_level=SUCCESS to hide the noisy INFO
            # cascade chatter while still seeing their wins.
            _emit = log_ok if log_ok else log
            if _emit:
                label = _SOURCE_LABELS.get(source, source)
                try:
                    _emit(f"    [PDF OK] {label} ({len(data)//1024}KB): {title}")
                except UnicodeEncodeError:
                    _emit(f"    [PDF OK] {label} ({len(data)//1024}KB)")
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
                    data = await self._try_url(client, url, cookies,
                                               log=log, tag="GS PDF")
                    if _ok(data, "gs_pdf"):
                        return cached

                # ── 1. Unpaywall (moved up — best free OA discovery service)
                if doi:
                    data = await self._try_unpaywall(client, doi)
                    if _ok(data, "unpaywall"):
                        return cached

                # ── 2. OpenAlex OA PDF
                if oa_pdf_url:
                    data = await self._try_url(client, oa_pdf_url,
                                               log=log, tag="OpenAlex OA")
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

                # ── 4. openAccessPdf (non-arxiv, non-doi direct link)
                #    Title-verify is still applied via _ok() default (skip_verify=False)
                #    since S2/OpenAlex sometimes hands back the wrong OA PDF.
                if pdf_url and "arxiv.org" not in pdf_url and "doi.org" not in pdf_url:
                    data = await self._try_url(client, pdf_url,
                                               log=log, tag="开放获取PDF")
                    if _ok(data, "openaccess"):
                        return cached

                # ── 5. S2 API lookup — query live openAccessPdf + enrich IDs
                #    Phase 2 already stored pdf_url from S2; the re-query here
                #    is useful when:
                #      - Phase 2 cache is stale (S2 updated OA info since)
                #      - s2_id was missing at Phase 2 but a title match works
                #      - openAccessPdf URL differs from the one in pdf_url
                #    Falls back to title search when s2_id is absent.
                s2_data = None
                if s2_id or full_title:
                    s2_data = await self._fetch_s2_data(client, s2_id, full_title)
                if s2_data:
                    # Supplement IDs first (benefits later arxiv / Sci-Hub steps)
                    ext = s2_data.get("externalIds") or {}
                    if not arxiv_id:
                        arxiv_id = ext.get("ArXiv", "") or arxiv_id
                    if not doi:
                        _d = ext.get("DOI", "")
                        if _d:
                            doi = _d.replace("https://doi.org/", "").replace(
                                "http://doi.org/", "").strip()

                    s2_pdf = (s2_data.get("openAccessPdf") or {}).get("url", "")
                    # Skip if it's the same URL step 4 already tried, or if it
                    # points to arxiv (step 8 handles arxiv with title-verify)
                    if s2_pdf and s2_pdf != pdf_url and "arxiv.org" not in s2_pdf:
                        data = await self._try_url(client, s2_pdf,
                                                   log=log, tag="S2 openAccessPdf")
                        if _ok(data, "s2_page"):
                            return cached

                # ── 6. DBLP conference lookup
                if full_title:
                    dblp_url = await self._fetch_dblp_pdf(client, full_title)
                    if dblp_url:
                        data = await self._try_url(client, dblp_url, _get_cookies_for_url(dblp_url))
                        if _ok(data, "dblp"):
                            return cached

                # ── 7. Sci-Hub
                #    Sci-Hub serves DOI→PDF, high fidelity. skip_verify OK.
                if doi:
                    data = await self._try_scihub(client, doi, log=log)
                    if _ok(data, "scihub", skip_verify=True):
                        return cached

                # ── 8. arXiv (by ID if known)
                #    CRITICAL: arxiv_id from S2/OpenAlex is often WRONG for recent
                #    papers (they mis-match DOIs → random arXiv IDs). We MUST verify
                #    the title; baseline "skip_verify=True" caused silent false
                #    positives (e.g. ECNet 2025 → arxiv 2106.13217 "Exploring Depth").
                if arxiv_id:
                    data = await self._try_url(client,
                        f"https://arxiv.org/pdf/{arxiv_id}",
                        log=log, tag="arXiv(元数据ID)")
                    if _ok(data, "arxiv"):
                        return cached

                # ── 8b. arXiv title search (when metadata didn't have arxiv_id,
                #    OR when metadata's arxiv_id was rejected by title match).
                #    Title-search match is inherently verified — we already compared
                #    titles when picking the candidate.
                if full_title:
                    found_id = await self._search_arxiv_by_title(client, full_title)
                    if found_id and found_id != arxiv_id:  # avoid re-trying the same bad ID
                        data = await self._try_url(client,
                            f"https://arxiv.org/pdf/{found_id}",
                            log=log, tag="arXiv(标题搜索)")
                        if _ok(data, "arxiv_search"):
                            arxiv_id = found_id  # remember for potential later use
                            return cached

                # ── 8c. OpenReview title search (ML/AI conference papers)
                #    The rewritten _search_openreview returns *concrete* PDF URLs
                #    from the note's `pdf` field (arXiv / CVF / AAAI / OR-hosted)
                #    and filters out DBLP-mirror entries that have no free PDF.
                #    We route by host: ScraperAPI only for openreview.net
                #    (Cloudflare-protected); everything else goes direct.
                if full_title:
                    or_candidates = await self._search_openreview(client, full_title)
                    for or_pdf in or_candidates:
                        if log:
                            log(f"    [OpenReview] 尝试: {or_pdf[:80]}")
                        host = urlparse(or_pdf).netloc.lower()
                        needs_scraperapi = "openreview.net" in host

                        if needs_scraperapi and self._scraper_keys:
                            scraper_url = (
                                f"https://api.scraperapi.com?api_key={self._scraper_keys[0]}"
                                f"&url={quote(or_pdf)}"
                            )
                            try:
                                sr = await client.get(scraper_url, timeout=30)
                                if sr.status_code == 200 and sr.content[:5] == b"%PDF-" and len(sr.content) > 1000:
                                    if _ok(sr.content, "openreview"):
                                        return cached
                            except Exception:
                                pass
                            # Fallback: direct (might work if Cloudflare mood is good)
                            data = await self._try_url(client, or_pdf,
                                                       log=log, tag="OpenReview")
                            if _ok(data, "openreview"):
                                return cached
                        else:
                            # arXiv / CVF / AAAI / publisher OA — direct is fine
                            data = await self._try_url(client, or_pdf,
                                                       log=log, tag="OpenReview")
                            if _ok(data, "openreview"):
                                return cached

                # ── 8d. GS "all versions" page scrape
                #    Phase 1 captured this URL; it lists every indexed version,
                #    typically including free mirrors (arXiv, .edu, ResearchGate)
                #    that are not in the canonical `paper_link`.
                if gs_all_versions:
                    gv_candidates = await self._fetch_gs_all_versions(client, gs_all_versions)
                    if gv_candidates and log:
                        log(f"    [GS版本页] 发现 {len(gv_candidates)} 个候选链接")
                    for cand in gv_candidates:
                        cand_url = cand["url"]
                        cand_kind = cand["kind"]
                        transformed = _transform_url(cand_url)
                        cookies = _get_cookies_for_url(transformed)
                        if log:
                            log(f"    [GS版本页] 尝试 {cand_kind}: {transformed[:80]}")
                        data = await self._try_url(client, transformed, cookies,
                                                   log=log, tag="GS版本页")
                        label = "gs_versions_pdf" if cand_kind == "pdf" else "gs_versions_link"
                        if _ok(data, label):
                            return cached

                # ── 8e. CORE aggregator search
                #    CORE indexes 270M+ papers from institutional repositories
                #    (.edu preprint servers, OA journals). Great last-chance
                #    rescue for papers where the author self-archived.
                #    Requires a free API key; silently skipped otherwise.
                if full_title and self._core_api_key:
                    core_cands = await self._search_core(client, full_title, doi)
                    if core_cands and log:
                        log(f"    [CORE] 发现 {len(core_cands)} 个候选")
                    for cand in core_cands:
                        if log:
                            repo = cand.get("repo_name", "?")
                            log(f"    [CORE] 尝试 ({repo}): {cand['url'][:80]}")
                        data = await self._try_url(client, cand["url"],
                                                   log=log, tag="CORE")
                        if _ok(data, "core"):
                            return cached

                # ── 8f. ResearchGate title search
                #    Authors upload their own copies (often author-accepted
                #    manuscripts / preprints). Heavily bot-blocked — needs
                #    ScraperAPI premium. We pre-filter search results by
                #    `availableFrom != null` so we only fetch pages that
                #    actually have a PDF.
                if full_title and self._scraper_keys:
                    rg_urls = await self._search_researchgate(client, full_title)
                    if rg_urls and log:
                        log(f"    [ResearchGate] 发现 {len(rg_urls)} 篇可下载候选")
                    for pub_url in rg_urls:
                        if log:
                            log(f"    [ResearchGate] 尝试: {pub_url[:80]}")
                        # RG publication page with premium (render causes 403)
                        scraper_url = (
                            f"https://api.scraperapi.com?api_key={self._scraper_keys[0]}"
                            f"&url={quote(pub_url)}&premium=true"
                        )
                        try:
                            sr = await client.get(scraper_url, timeout=60)
                            if sr.status_code != 200 or len(sr.content) < 500:
                                # 2026-04-21: was silent. Typical cause:
                                # ScraperAPI premium returns 500 when RG
                                # bot-check page is served, or RG returns
                                # a 200 with ~0 bytes (unavailable region).
                                if log:
                                    log(f"    [ResearchGate] 页面获取失败"
                                        f" HTTP {sr.status_code},"
                                        f" {len(sr.content)}B")
                                continue
                            # Direct PDF inline?
                            if sr.content[:5] == b"%PDF-":
                                if _ok(sr.content, "researchgate"):
                                    return cached
                                continue
                            html = sr.text
                            # RG pdf URLs are typically in the form
                            # /profile/<NAME>/publication/<ID>/links/<HASH>/<slug>.pdf
                            # embedded in JSON blobs as \/profile\/...
                            pdf_link = None
                            m = re.search(
                                r'"fullTextDownloadUrl":"([^"]+)"', html)
                            if m:
                                pdf_link = m.group(1).replace("\\/", "/")
                            if not pdf_link:
                                m = re.search(
                                    r'(/profile/[^"\s]+?/publication/\d+/links/[0-9a-f]+/[^"\s]+?\.pdf)',
                                    html)
                                if m:
                                    pdf_link = "https://www.researchgate.net" + m.group(1)
                            if not pdf_link:
                                pdf_link = _extract_pdf_url_from_html(html, pub_url)
                            if not pdf_link:
                                if log:
                                    log(f"    [ResearchGate] 页面无 PDF 链接 (可能需要作者授权)")
                                continue
                            # Fetch the PDF via ScraperAPI premium
                            scraper_pdf = (
                                f"https://api.scraperapi.com?api_key={self._scraper_keys[0]}"
                                f"&url={quote(pdf_link)}&premium=true"
                            )
                            pr = await client.get(scraper_pdf, timeout=90)
                            if pr.status_code == 200 and pr.content[:5] == b"%PDF-":
                                if _ok(pr.content, "researchgate"):
                                    return cached
                                # _ok() already logs the reason (mojibake /
                                # title-mismatch / too-small). Fall through.
                            else:
                                # 2026-04-21: was silent. Most common
                                # cause: 403 from RG PDF CDN, or %PDF-
                                # bytes not present (error page).
                                if log:
                                    body_first = pr.content[:5]
                                    log(f"    [ResearchGate] PDF 下载"
                                        f" HTTP {pr.status_code},"
                                        f" 开头={body_first!r}")
                        except Exception as e:
                            # 2026-04-21: was silent. Timeout / connect
                            # failures / bad-response exceptions now
                            # identifiable by class name.
                            if log:
                                log(f"    [ResearchGate] 异常"
                                    f" {type(e).__name__}: {str(e)[:80]}")

                # ── 9. GS paper_link + smart URL transform
                if paper_link and "scholar.google" not in paper_link:
                    transformed = _transform_url(paper_link)
                    cookies = _get_cookies_for_url(transformed)
                    data = await self._try_url(client, transformed, cookies,
                                               log=log, tag="GS链接")
                    if _ok(data, "gs_link"):
                        return cached
                    # If transform didn't change URL, also try original
                    if transformed != paper_link:
                        cookies2 = _get_cookies_for_url(paper_link)
                        data = await self._try_url(client, paper_link, cookies2,
                                                   log=log, tag="GS原链接")
                        if _ok(data, "gs_link"):
                            return cached

                # ── 10. DOI redirect (cheap attempt before expensive ScraperAPI)
                if doi:
                    doi_url = f"https://doi.org/{doi}"
                    cookies = _get_cookies_for_url(doi_url)
                    data = await self._try_url(client, doi_url, cookies,
                                               log=log, tag="DOI跳转")
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

        # ── 12. CDP browser session (IEEE/Elsevier — real browser with auth)
        # Uses Chrome DevTools Protocol to download via authenticated browser.
        # Requires: cdp_debug_port > 0 and websocket-client installed.
        #
        # 2026-04-21: added visibility logs. Previously the tier would
        # silently skip when cdp_debug_port=0, websocket-client missing,
        # or the publisher gate didn't match (e.g. Elsevier DOI without
        # a sciencedirect paper_link). Users looking at [PDF失败] trace
        # saw NO mention of CDP and wondered if it even tried.
        if not self._cdp_debug_port:
            pass  # no-op: logged once at pipeline start, no need to repeat
        elif not _cdp_available():
            if log:
                log("    [CDP] websocket-client 未安装，CDP 通道不可用")
        else:
            # Gate variables split out so we can log each decision.
            _is_ieee = bool(paper_link and "ieeexplore.ieee.org" in paper_link)
            _is_elsevier = bool(paper_link and (
                "sciencedirect.com" in paper_link
                or _pub_from_doi == "elsevier"
            )) or (not paper_link and _pub_from_doi == "elsevier")
            if _is_ieee:
                data = await self._try_cdp_ieee(paper, log=log)
                if _ok(data, "cdp_ieee"):
                    return cached
            elif _pub_from_doi == "ieee" or _pub_from_link == "ieee":
                if log:
                    log(f"    [CDP-IEEE] 跳过: paper_link 不是 ieeexplore 域 "
                        f"(link={paper_link[:60]!r})")
            if _is_elsevier:
                # _try_cdp_elsevier internally requires /pii/XXX in
                # paper_link. Pass through but also log if we're about
                # to call it with a link that lacks pii.
                if paper_link and "/pii/" not in paper_link:
                    if log:
                        log(f"    [CDP-Elsevier] 跳过: paper_link 无 /pii/ "
                            f"段 (link={paper_link[:60]!r})")
                elif not paper_link:
                    if log:
                        log("    [CDP-Elsevier] 跳过: 无 paper_link "
                            "(Elsevier DOI 但 GS 没给 pii URL)")
                else:
                    data = await self._try_cdp_elsevier(paper, log=log)
                    if _ok(data, "cdp_elsevier"):
                        return cached
            elif _pub_from_doi == "elsevier" or _pub_from_link == "elsevier":
                # Shouldn't reach here due to _is_elsevier OR above, but
                # defensive in case gate logic changes.
                if log:
                    log("    [CDP-Elsevier] 跳过: publisher gate 不满足")

        # ── 13. LLM search for alternative PDF (preprints, author pages, repos)
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

        # ── 14. curl + socks5 + Chrome cookies (legacy fallback)
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

        # ── 15. ScraperAPI + LLM smart fallback (last resort for non-publisher pages)
        # Previously wrote to cache with a raw `%PDF-` check which bypassed
        # the mojibake guard and title verification (2026-04-20 regression:
        # MDPI Paper 5 passed here with a mojibake'd PDF). Now gated by
        # `_ok()` which runs both checks.
        if paper_link and "scholar.google" not in paper_link and not _is_publisher_paper:
            data = await self._smart_scraper_download(paper_link)
            if _ok(data, "scraper_smart"):
                return cached

        return None  # All sources exhausted for this attempt

    # ── Helper: arXiv title search ──────────────────────────────────
    async def _search_arxiv_by_title(self, client, title: str) -> Optional[str]:
        """Search arXiv API by title, return arxiv_id if a good match is found."""
        try:
            clean = re.sub(r'[^\w\s]', ' ', title)
            url = f"https://export.arxiv.org/api/query?search_query=ti:{quote(clean)}&max_results=3"
            await asyncio.sleep(0.35)  # arXiv rate limit: 3 req/s
            resp = await client.get(url, timeout=15)
            if resp.status_code != 200:
                return None
            from xml.etree import ElementTree as ET
            root = ET.fromstring(resp.text)
            ns = "{http://www.w3.org/2005/Atom}"
            _stop = {'a','an','the','of','in','on','for','and','or','to',
                     'with','by','is','are','from','at','as','its','via','using'}
            title_words = set(re.sub(r'[^\w\s]', ' ', title.lower()).split()) - _stop
            if len(title_words) < 2:
                return None
            for entry in root.findall(f"{ns}entry"):
                etitle = entry.findtext(f"{ns}title", "").strip().replace("\n", " ")
                e_words = set(re.sub(r'[^\w\s]', ' ', etitle.lower()).split()) - _stop
                if not e_words:
                    continue
                overlap = len(title_words & e_words) / len(title_words)
                if overlap >= 0.7:
                    eid = entry.findtext(f"{ns}id", "")
                    m = re.search(r'(\d{4}\.\d{4,5})', eid)
                    if m:
                        return m.group(1)
        except Exception:
            pass
        return None

    # ── Helper: OpenReview title search ──────────────────────────────
    # Per-instance memoization — cascade calls this up to 3× per paper on retry.
    # reset per-run; not cross-run since the underlying index changes.
    async def _search_openreview(self, client, title: str) -> List[str]:
        """Search OpenReview API by title, return candidate PDF URLs.

        OpenReview hosts real submissions (ICLR/NeurIPS/ACMM/AAAI/CVF) AND
        DBLP-mirror metadata entries. For real submissions the note has a
        ``pdf`` field:
          - starts with 'http'  → external free PDF (arXiv / CVF / publisher OA)
          - starts with '/pdf/' → hash-named PDF hosted on openreview.net
        DBLP-mirror entries have NO ``pdf`` field — for these, constructing
        ``/pdf?id={forum_id}`` (what the old code did) either 404s or
        redirects to a publisher paywall. The 2026-04 reliability test showed
        66% of the old code's candidates were un-fetchable because of this.

        Returns a deduplicated list of concrete PDF URLs. ScraperAPI fallback
        kicks in if the direct API is Cloudflare-blocked.
        """
        _stop = {'a','an','the','of','in','on','for','and','or','to',
                 'with','by','is','are','from','at','as','its','via','using'}
        title_words = set(re.sub(r'[^\w\s]', ' ', title.lower()).split()) - _stop
        if len(title_words) < 2:
            return []

        # Per-title memoization — cascade retries should reuse the API result.
        cache_key = f"or::{title.lower().strip()}"
        if not hasattr(self, "_openreview_cache"):
            self._openreview_cache = {}
        if cache_key in self._openreview_cache:
            return self._openreview_cache[cache_key]

        def _get_value(field):
            """API v2 wraps most string fields in {'value': '...'}; v1 doesn't."""
            if isinstance(field, dict):
                return field.get("value", "")
            return field or ""

        def _match_notes(data: dict) -> List[str]:
            urls = []
            seen = set()
            notes = data.get("notes", [])
            for note in notes:
                content = note.get("content", {})
                note_title = _get_value(content.get("title", ""))
                if not note_title:
                    continue
                # Fuzzy title match to filter out unrelated results
                n_words = set(re.sub(r'[^\w\s]', ' ', note_title.lower()).split()) - _stop
                if not n_words:
                    continue
                overlap = len(title_words & n_words) / len(title_words)
                if overlap < 0.7:
                    continue

                # Use the API-provided pdf URL when present — it points to
                # whichever host actually stores the PDF (arXiv, CVF, AAAI,
                # OpenReview-hosted /pdf/<hash>.pdf, or publisher).
                pdf_field = _get_value(content.get("pdf", ""))
                pdf_url = None
                if pdf_field:
                    if pdf_field.startswith("http"):
                        pdf_url = pdf_field
                    elif pdf_field.startswith("/pdf/"):
                        pdf_url = f"https://openreview.net{pdf_field}"
                    elif pdf_field.startswith("/attachment/"):
                        pdf_url = f"https://openreview.net{pdf_field}"

                if pdf_url:
                    # Skip publisher-paywall direct URLs — 2026-04 reliability
                    # test showed 17/17 IEEE iel7/iel8/*.pdf and Springer
                    # /content/pdf/... URLs surfaced by OpenReview all hit
                    # institutional login walls. These are retried later in
                    # the cascade via ``_scraper_publisher_download`` (which
                    # uses ultra_premium + publisher-specific extraction),
                    # so fetching them here is pure wasted latency.
                    pl = pdf_url.lower()
                    paywall = (
                        "/iel7/" in pl or "/iel8/" in pl or "/iel9/" in pl
                        or "ieeexplore.ieee.org/stamp" in pl
                        or "sciencedirect.com" in pl
                        or "link.springer.com/content/pdf/" in pl
                        or "onlinelibrary.wiley.com/doi/pdf" in pl
                    )
                    if paywall:
                        continue
                    if pdf_url not in seen:
                        seen.add(pdf_url)
                        urls.append(pdf_url)
                    continue  # Have a concrete URL — skip the /pdf?id fallback

                # No pdf field. For REAL OpenReview submissions (venueid starts
                # with conference name like "ICLR.cc/..." or
                # "OpenReview.net/Archive") the /pdf?id endpoint usually works.
                # For DBLP-mirror entries (venueid "dblp.org/...") it doesn't —
                # skip them to avoid wasted fetches.
                venueid = _get_value(content.get("venueid", ""))
                if venueid and venueid.startswith("dblp.org"):
                    continue

                forum_id = note.get("forum") or note.get("id", "")
                if forum_id:
                    url = f"https://openreview.net/pdf?id={forum_id}"
                    if url not in seen:
                        seen.add(url)
                        urls.append(url)
            return urls

        # Build search URL (v2 API is current; v1 is legacy fallback)
        search_urls = [
            f"https://api2.openreview.net/notes/search?query={quote(title)}&limit=5",
            f"https://api.openreview.net/notes/search?term={quote(title)}&content=all&source=forum&limit=5",
        ]
        result: List[str] = []
        try:
            for api_url in search_urls:
                resp = await client.get(api_url, timeout=15)
                if resp.status_code == 200:
                    r = _match_notes(resp.json())
                    if r:
                        result = r
                        break
                elif resp.status_code == 403 and self._scraper_keys:
                    # Cloudflare blocked → go through ScraperAPI
                    scraper_url = (
                        f"https://api.scraperapi.com?api_key={self._scraper_keys[0]}"
                        f"&url={quote(api_url)}"
                    )
                    resp2 = await client.get(scraper_url, timeout=30)
                    if resp2.status_code == 200:
                        try:
                            r = _match_notes(resp2.json())
                            if r:
                                result = r
                        except Exception:
                            pass
                    break
        except Exception:
            pass

        self._openreview_cache[cache_key] = result
        return result

    # ── Helper: CORE aggregator search ─────────────────────────────────
    # CORE (core.ac.uk) indexes 270M+ papers from institutional repositories,
    # arXiv, PubMed, etc. — a huge second-chance source for papers whose
    # author has self-archived on their university page. Free tier: 1000
    # req/day with an API key (register at https://core.ac.uk/services/api).
    #
    # We prefer DOI-lookup first (deterministic); fall back to title search.
    # For each hit we try the `downloadUrl` / `fullTextIdentifier` fields;
    # both point to the repo-hosted PDF when the paper is OA.
    async def _search_core(self, client, title: str,
                           doi: str = "") -> List[dict]:
        """Return list of {url, source_id, repo_name} candidates from CORE.

        Results are ordered by title-match confidence. Empty list if no key.
        """
        if not self._core_api_key:
            return []
        if not title and not doi:
            return []

        cache_key = f"core::{(doi or '').lower()}::{(title or '').lower().strip()}"
        if not hasattr(self, "_core_cache"):
            self._core_cache = {}
        if cache_key in self._core_cache:
            return self._core_cache[cache_key]

        _stop = {'a','an','the','of','in','on','for','and','or','to',
                 'with','by','is','are','from','at','as','its','via','using'}
        title_words = set(re.sub(r'[^\w\s]', ' ', title.lower()).split()) - _stop

        headers = {"Authorization": f"Bearer {self._core_api_key}"}
        candidates: List[dict] = []
        seen_urls = set()

        def _best_url(hit: dict) -> Optional[str]:
            """Pick the best PDF-ish URL from a CORE work record."""
            # CORE returns `downloadUrl` for the repo-hosted PDF
            u = hit.get("downloadUrl", "") or ""
            if u:
                return u
            # Fall back to fullTextIdentifier
            u = hit.get("fullTextIdentifier", "") or ""
            if u:
                return u
            # URLs in the `urls` array (list of {url, type})
            for rec in (hit.get("urls") or []):
                ru = rec.get("url", "") if isinstance(rec, dict) else rec
                if isinstance(ru, str) and ru:
                    return ru
            return None

        def _collect(data: dict):
            results = data.get("results") or data.get("data") or []
            for hit in results:
                ht = hit.get("title", "") or ""
                if not ht:
                    continue
                # Fuzzy title match
                hw = set(re.sub(r'[^\w\s]', ' ', ht.lower()).split()) - _stop
                if title_words and hw:
                    overlap = len(title_words & hw) / len(title_words)
                    if overlap < 0.7:
                        continue
                url = _best_url(hit)
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                candidates.append({
                    "url": url,
                    "source_id": hit.get("id", ""),
                    "repo_name": (hit.get("repositoryDocument") or {}).get("repositoryName", ""),
                })

        # Path 1: DOI search (deterministic)
        if doi:
            try:
                doi_clean = doi.replace("https://doi.org/", "").strip()
                url = f"https://api.core.ac.uk/v3/search/works?q=doi:%22{quote(doi_clean)}%22&limit=3"
                resp = await client.get(url, headers=headers, timeout=20)
                if resp.status_code == 200:
                    _collect(resp.json())
                elif resp.status_code == 429:
                    # rate-limited — back off a bit, we'll still try the title
                    await asyncio.sleep(2.0)
            except Exception:
                pass

        # Path 2: title search (covers papers with no DOI or new DOI not yet indexed)
        if title and len(candidates) < 3:
            try:
                url = f"https://api.core.ac.uk/v3/search/works?q=title:%22{quote(title[:200])}%22&limit=5"
                resp = await client.get(url, headers=headers, timeout=20)
                if resp.status_code == 200:
                    _collect(resp.json())
            except Exception:
                pass

        self._core_cache[cache_key] = candidates
        return candidates

    # ── Helper: ResearchGate title search ───────────────────────────────
    # RG aggressively blocks bots (Cloudflare + fingerprinting), so we go
    # through ScraperAPI with render=true. The search page is a Next.js
    # SPA — the results aren't in the initial HTML; after render they sit
    # in JSON-escaped blobs: `"publication":{"url":"publication\/NNN_Slug", ...}`.
    async def _search_researchgate(self, client, title: str) -> List[str]:
        """Return list of ResearchGate publication URLs matching the title."""
        if not title or len(title) < 10:
            return []
        if not self._scraper_keys:
            return []  # RG blocks direct — ScraperAPI required

        cache_key = f"rg::{title.lower().strip()}"
        if not hasattr(self, "_rg_cache"):
            self._rg_cache = {}
        if cache_key in self._rg_cache:
            return self._rg_cache[cache_key]

        _stop = {'a','an','the','of','in','on','for','and','or','to',
                 'with','by','is','are','from','at','as','its','via','using'}
        tw = set(re.sub(r'[^\w\s]', ' ', title.lower()).split()) - _stop

        search_url = (
            f"https://www.researchgate.net/search/publication"
            f"?q={quote(title[:200])}"
        )
        scraper_url = (
            f"https://api.scraperapi.com?api_key={self._scraper_keys[0]}"
            f"&url={quote(search_url)}&render=true"
        )
        urls: List[str] = []
        try:
            resp = await client.get(scraper_url, timeout=90)
            if resp.status_code != 200:
                self._rg_cache[cache_key] = urls
                return urls
            html = resp.text

            # Extract publication records.
            # RG search result JSON has nested {} (authors, previewImage etc.)
            # so a simple `{...}` regex can't match full blocks. Instead we
            # find each `"publication":{"url":"publication\/..."` anchor and
            # scan ~2000 chars ahead for availableFrom / title.
            seen = set()
            for m in re.finditer(
                r'"publication":\{[^"]{0,30}"url":"publication\\?/(\d+_[^"?]+)',
                html,
            ):
                slug = m.group(1)
                window = html[m.start(): m.start() + 2500]
                mt = re.search(r'"title":"([^"]+)"', window)
                ma = re.search(r'"availableFrom":(?:null|"([^"]*)")', window)
                if not mt:
                    continue
                # availableFrom is null → RG has only metadata, skip
                if not (ma and ma.group(1)):
                    continue
                rg_title = (mt.group(1).replace("\\u0026", "&")
                                       .replace("\\u201c", '"')
                                       .replace("\\u201d", '"')
                                       .replace("\\/", "/"))
                # Fuzzy title match — RG often reformats titles slightly
                rt_words = set(re.sub(r'[^\w\s]', ' ', rg_title.lower()).split()) - _stop
                if tw and rt_words:
                    ov = len(tw & rt_words) / len(tw)
                    if ov < 0.6:
                        continue
                pub_url = f"https://www.researchgate.net/publication/{slug}"
                if pub_url not in seen:
                    seen.add(pub_url)
                    urls.append(pub_url)
                if len(urls) >= 3:
                    break
        except Exception:
            pass

        self._rg_cache[cache_key] = urls
        return urls

    # ── Helper: Google Scholar "all versions" page scraping ──────────
    # GS's "/scholar?cluster=..." page lists EVERY version GS has indexed —
    # typically includes free mirrors on arXiv, author homepages, .edu repos,
    # ResearchGate, etc. Phase 1 already captures this URL as `gs_all_versions`
    # but nobody reads it. This is the single largest untapped free source.
    #
    # Strategy:
    #   1. Fetch the versions page via ScraperAPI (GS blocks direct)
    #   2. Extract two kinds of links per version block:
    #      a. Right-side [PDF] sidebar link  (div.gs_or_ggsm a / div.gs_ggs a)
    #      b. Main title link  (h3.gs_rt a)
    #   3. Prioritize candidates by domain: arXiv/CVF/ACL/OpenReview/edu
    #      above publisher-paywall domains (IEEE/Elsevier/Springer/ACM)
    #   4. Dedupe, return ordered candidate list. Caller tries each with
    #      title verification.
    async def _fetch_gs_all_versions(self, client, gs_versions_url: str) -> List[dict]:
        """Return ordered list of {url, kind, domain} candidates from GS versions page.

        kind: "pdf" = sidebar [PDF] link (most likely actual PDF)
              "link" = main title link (may be HTML landing page, needs extraction)
        """
        if not gs_versions_url or "scholar.google" not in gs_versions_url:
            return []
        if not self._scraper_keys:
            return []  # GS blocks direct requests; ScraperAPI is required
        # Return memoized result (download cascade retries up to 3 times)
        if gs_versions_url in self._gs_versions_cache:
            return self._gs_versions_cache[gs_versions_url]

        # Fetch via ScraperAPI (GS requires JS render to avoid captcha hints)
        scraper_url = (
            f"https://api.scraperapi.com?api_key={self._scraper_keys[0]}"
            f"&url={quote(gs_versions_url)}"
        )
        try:
            resp = await client.get(scraper_url, timeout=45)
            if resp.status_code != 200:
                return []
            html = resp.text
            if len(html) < 500 or "gs_r" not in html:
                return []
        except Exception:
            return []

        # Parse each <div class="gs_r gs_or gs_scl"> block
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
        except ImportError:
            soup = None

        candidates: List[dict] = []
        seen: set = set()

        def _add(url: str, kind: str):
            if not url or url in seen:
                return
            if not url.startswith("http"):
                return
            # Skip obviously non-usable
            if any(s in url for s in ["scholar.google.", "/scholar?", "javascript:"]):
                return
            seen.add(url)
            host = urlparse(url).netloc.lower()
            candidates.append({"url": url, "kind": kind, "domain": host})

        if soup is not None:
            for block in soup.select("div.gs_r.gs_or.gs_scl, div.gs_r.gs_or"):
                # Sidebar [PDF] direct link
                for a in block.select("div.gs_or_ggsm a, div.gs_ggs a"):
                    _add(a.get("href", ""), "pdf")
                # Main title link
                title_a = block.select_one("h3.gs_rt a")
                if title_a:
                    _add(title_a.get("href", ""), "link")
        else:
            # Regex fallback (bs4 not installed)
            for m in re.finditer(
                r'<div class="gs_or_ggsm"[^>]*>.*?<a[^>]+href="([^"]+)"',
                html, re.DOTALL,
            ):
                _add(m.group(1), "pdf")
            for m in re.finditer(
                r'<h3 class="gs_rt"[^>]*>.*?<a[^>]+href="([^"]+)"',
                html, re.DOTALL,
            ):
                _add(m.group(1), "link")

        # Priority ordering:
        #   tier 1: PDF-direct + free-OA domains (arxiv/CVF/ACL/OpenReview/mdpi)
        #   tier 2: PDF-direct + unknown (.edu/.org/repos/researchgate)
        #   tier 3: main-link + free-OA domains
        #   tier 4: PDF-direct + publisher domains (skip — same as paper_link)
        #   tier 5: main-link + everything else
        _FREE_OA = (
            "arxiv.org", "openaccess.thecvf.com", "aclanthology.org",
            "openreview.net", "mdpi.com", "hindawi.com",
            "frontiersin.org", "papers.nips.cc", "proceedings.mlr.press",
            "proceedings.neurips.cc", "bmva-archive.org.uk",
            "authorea.com", "techrxiv.org", "biorxiv.org", "medrxiv.org",
            "papers.ssrn.com",
        )
        _PUBLISHER = (
            "ieeexplore.ieee.org", "sciencedirect.com", "link.springer.com",
            "dl.acm.org", "onlinelibrary.wiley.com", "tandfonline.com",
        )

        def _tier(c: dict) -> int:
            d = c["domain"]
            is_free = any(f in d for f in _FREE_OA)
            is_pub = any(p in d for p in _PUBLISHER)
            is_pdf = c["kind"] == "pdf"
            if is_pdf and is_free:
                return 1
            if is_pdf and not is_pub:
                return 2
            if not is_pdf and is_free:
                return 3
            if is_pdf and is_pub:
                return 4
            return 5

        candidates.sort(key=_tier)
        result = candidates[:12]  # Cap to avoid excessive requests
        self._gs_versions_cache[gs_versions_url] = result
        return result

    # ── Helper: fetch S2 data by ID or title ──────────────────────────
    # Rate limits (per S2 docs, 2026-04):
    #   No API key:  1 req/s (strict — exceeding triggers 429)
    #   With key:    100 req/s (plenty for concurrent downloads)
    # We gate concurrent callers with a lock regardless, but the sleep
    # interval between calls scales with the key presence.
    _s2_dl_lock = asyncio.Lock()

    async def _fetch_s2_data(self, client, s2_id: str, title: str) -> Optional[dict]:
        """Get S2 paper data (openAccessPdf, externalIds) by ID or (fuzzy) title.

        Caches by (s2_id, normalised title) so the cascade retry doesn't
        re-query. Uses the API key header when available for 100× higher
        rate-limit — dropping cumulative waits from ~60s to <1s for a
        typical 56-paper run.
        """
        cache_key = (s2_id or "", (title or "").strip().lower())
        if cache_key in self._s2_cache:
            return self._s2_cache[cache_key]

        fields = "openAccessPdf,externalIds,title"
        if s2_id:
            url = f"https://api.semanticscholar.org/graph/v1/paper/{s2_id}?fields={fields}"
        elif title:
            url = (f"https://api.semanticscholar.org/graph/v1/paper/search"
                   f"?query={quote(title)}&limit=1&fields={fields}")
        else:
            return None

        headers = {}
        if self._s2_api_key:
            headers["x-api-key"] = self._s2_api_key
        sleep_s = 0.05 if self._s2_api_key else 1.1

        data: Optional[dict] = None
        try:
            async with self._s2_dl_lock:
                await asyncio.sleep(sleep_s)
                resp = await client.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                body = resp.json()
                # Search endpoint wraps results in {"data": [...], "total": N}
                if "data" in body:
                    if body["data"]:
                        data = body["data"][0]
                    # empty list → no result; leave data as None
                else:
                    # Direct paper lookup: response IS the paper record
                    data = body
            # 429 = rate-limited; back off and retry once
            elif resp.status_code == 429:
                await asyncio.sleep(2.0)
                resp2 = await client.get(url, headers=headers, timeout=15)
                if resp2.status_code == 200:
                    body = resp2.json()
                    if "data" in body:
                        if body["data"]:
                            data = body["data"][0]
                    else:
                        data = body
        except Exception:
            pass

        self._s2_cache[cache_key] = data
        return data

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

    # ── Helper: Sci-Hub (tries curl+socks5 → httpx direct → ScraperAPI) ──
    async def _try_scihub(self, client, doi: str, log=None) -> Optional[bytes]:
        """Try Sci-Hub mirrors for DOI.

        Layer 1: curl+socks5 (fast when user has a SOCKS5 proxy)
        Layer 2: httpx direct (works outside China; some mirrors are CDN-fronted)
        Layer 3: ScraperAPI (US IP — ScraperAPI fetches sci-hub for us)
        Race parallel per-layer so dead mirrors don't stall the whole cascade.
        """
        def _dbg(msg: str):
            if log:
                try:
                    log(f"    [Sci-Hub] {msg}")
                except UnicodeEncodeError:
                    pass

        # ── Layer 1: curl+socks5 (only if proxy configured) ──
        if _SOCKS_PROXY:
            _dbg(f"SOCKS 代理尝试 {len(SCIHUB_MIRRORS)} 个镜像")
            for mirror in SCIHUB_MIRRORS:
                try:
                    data = await self._curl_scihub(mirror, doi)
                    if data and data[:5] == b"%PDF-":
                        return data
                except Exception:
                    continue

        # ── Layer 2: httpx direct (short timeout, first success wins) ──
        async def _one_mirror(mirror: str) -> Optional[bytes]:
            try:
                resp = await client.get(f"{mirror}/{doi}", timeout=8)
                if resp.status_code != 200:
                    return None
                if resp.content[:5] == b"%PDF-":
                    return resp.content
                ctype = resp.headers.get("content-type", "")
                if "html" not in ctype:
                    return None
                html = resp.text
                if _scihub_article_missing(html):
                    return None
                pdf_url = _extract_scihub_pdf_url(html, str(resp.url))
                if not pdf_url:
                    return None
                r2 = await client.get(pdf_url, timeout=15)
                if r2.status_code == 200 and r2.content[:5] == b"%PDF-":
                    return r2.content
            except Exception:
                pass
            return None

        # Race all mirrors in parallel; take first PDF
        _dbg(f"并行尝试 {len(SCIHUB_MIRRORS)} 个镜像直连 (15s 超时)")
        tasks = [asyncio.create_task(_one_mirror(m)) for m in SCIHUB_MIRRORS]
        try:
            for coro in asyncio.as_completed(tasks, timeout=20):
                try:
                    data = await coro
                except Exception:
                    continue
                if data and data[:5] == b"%PDF-":
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    _dbg("直连镜像成功获取 PDF")
                    return data
        except asyncio.TimeoutError:
            for t in tasks:
                if not t.done():
                    t.cancel()

        # ── Layer 3: ScraperAPI proxy for China users ──
        if self._scraper_keys:
            _dbg(f"直连失败, 通过 ScraperAPI 尝试前 3 个镜像")
            key = self._scraper_keys[0]
            for mirror in SCIHUB_MIRRORS[:3]:  # Only try top 3 via ScraperAPI (cost)
                try:
                    scraper_url = (
                        f"https://api.scraperapi.com?api_key={key}"
                        f"&url={quote(f'{mirror}/{doi}')}"
                    )
                    resp = await client.get(scraper_url, timeout=30)
                    if resp.status_code != 200:
                        continue
                    if resp.content[:5] == b"%PDF-":
                        return resp.content
                    html = resp.text
                    if _scihub_article_missing(html):
                        continue
                    pdf_url = _extract_scihub_pdf_url(html, str(resp.url))
                    if not pdf_url:
                        continue
                    scraper_pdf = (
                        f"https://api.scraperapi.com?api_key={key}"
                        f"&url={quote(pdf_url)}"
                    )
                    r2 = await client.get(scraper_pdf, timeout=45)
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
                if _scihub_article_missing(html):
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
