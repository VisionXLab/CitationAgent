"""cdp_login_probe -- per-publisher CDP authentication diagnostic.

Tests whether the live debug browser (port N) has usable session cookies
for IEEE / ACM / Elsevier / Springer / Wiley. Called two ways:

1. Standalone CLI (`eval_toolkit/cdp_login_probe.py`) -- a thin wrapper
   that prints a human-readable table after `probe_all()`.
2. Inline from `TaskExecutor._prompt_phase2_login` after the login
   checkpoint times out / user clicks 继续. Results get appended to
   `run.log` so users see auth status BEFORE the 20-min download phase
   -- huge time-saver when, e.g., ACM still wants step-up auth.

State machine produced per publisher:

  PDF_OK           full success: landing loaded + PDF bytes fetched
  AUTH_OK          landing loaded the real paper; PDF fetch via probe's
                   simple URL failed, but auth is evidently working.
                   Publisher-specific PDF URL flows (Elsevier pdfft md5,
                   Springer SharedIt etc.) are too complex to replicate
                   in a 5-line probe; harness has dedicated tiers.
  CAPTCHA          landing is stuck on a Cloudflare / Akamai / PerimeterX
                   challenge page ("Just a moment..." / "请稍候..." /
                   Turnstile). Auth might be fine, but the real download
                   path will be blocked until the user manually passes
                   the challenge in the browser.
  LOGIN_WALL       landing URL redirected to /login /signin, or
                   document.title contains sign-in markers.
  FIXTURE_BROKEN   landing is a 404 / error page -- probe's DOI fixture
                   is stale, NOT an auth issue.
  MOJIBAKE         PDF downloaded but content streams corrupt
                   (ScraperAPI-style byte mangling).
  ERROR            CDP helper / network error.

Thread-safety: probe_all() is synchronous and blocking. If you need
asyncio context, wrap in `asyncio.to_thread(probe_all, port, ...)`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Callable


# Hand-picked per-publisher test papers. Selection criteria:
#   - widely cited, stable DOI (won't disappear in 6 months)
#   - paywalled on publisher page so unauthenticated fetch will NOT
#     succeed -- lets probe distinguish "logged in" from "no cookies"
#   - title is distinctive enough that title-match won't false-positive
#
# VERIFIED 2026-04-20 via live probe against port 9222.
PUBLISHER_PROBES = {
    "ieee": {
        "doi": "10.1109/CVPR.2016.90",
        "title": "Deep Residual Learning for Image Recognition",
        "landing_url": "https://ieeexplore.ieee.org/document/7780459",
        # IMPORTANT: the real PDF endpoint is stampPDF/getPDF.jsp (matches
        # _try_cdp_ieee in pdf_downloader.py). stamp.jsp is the HTML
        # landing page that DECIDES auth -- if you fetch it you get HTML,
        # not PDF bytes, and the probe false-negatives to LOGIN.
        "pdf_url": "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=7780459&ref=",
    },
    "acm": {
        # Grover & Leskovec, "node2vec: Scalable Feature Learning for
        # Networks", KDD 2016. Very widely cited, stable DOI.
        "doi": "10.1145/2939672.2939754",
        "title": "node2vec: Scalable Feature Learning for Networks",
        "landing_url": "https://dl.acm.org/doi/10.1145/2939672.2939754",
        "pdf_url": "https://dl.acm.org/doi/pdf/10.1145/2939672.2939754",
    },
    "elsevier": {
        "doi": "10.1016/j.neunet.2014.09.003",
        "title": "Deep learning in neural networks",
        "landing_url": "https://www.sciencedirect.com/science/article/pii/S0893608014002135",
        # Elsevier's REAL pdfft URL needs md5+pid+pii from the
        # pdfDownload React-state metadata embedded in the landing HTML.
        # Probe's simple /pdfft is the "dumb" fallback -- AUTH_OK status
        # is the more reliable signal for this publisher.
        "pdf_url": "https://www.sciencedirect.com/science/article/pii/S0893608014002135/pdfft",
    },
    "springer": {
        # Russakovsky et al., "ImageNet Large Scale Visual Recognition
        # Challenge", IJCV 2015. Live-probed title confirmed.
        "doi": "10.1007/s11263-015-0816-y",
        "title": "ImageNet Large Scale Visual Recognition Challenge",
        "landing_url": "https://link.springer.com/article/10.1007/s11263-015-0816-y",
        "pdf_url": "https://link.springer.com/content/pdf/10.1007%2Fs11263-015-0816-y.pdf",
    },
    "wiley": {
        # Grigorescu et al., "A survey of deep learning techniques for
        # autonomous driving", J. Field Robotics 2020.
        "doi": "10.1002/rob.21918",
        "title": "A survey of deep learning techniques for autonomous driving",
        "landing_url": "https://onlinelibrary.wiley.com/doi/10.1002/rob.21918",
        "pdf_url": "https://onlinelibrary.wiley.com/doi/pdf/10.1002/rob.21918",
    },
}

# Valid status codes. Exposed so callers can treat them as string keys.
STATUS_PDF_OK = "PDF_OK"
STATUS_AUTH_OK = "AUTH_OK"
STATUS_CAPTCHA = "CAPTCHA"
STATUS_LOGIN_WALL = "LOGIN_WALL"
STATUS_FIXTURE_BROKEN = "FIXTURE_BROKEN"
STATUS_MOJIBAKE = "MOJIBAKE"
STATUS_ERROR = "ERROR"

# Subset of statuses that indicate auth is working (counted as "passed"
# in summary rollups; consumers can import this to decide). CAPTCHA is
# DELIBERATELY NOT in this set -- Cloudflare-stuck pages mean the real
# download path will hang for 120s before timing out, even if session
# cookies are valid.
PASSING_STATUSES = frozenset({STATUS_PDF_OK, STATUS_AUTH_OK})

# Cloudflare / Akamai / PerimeterX challenge-page markers.
# Title/body substrings (case-insensitive) that unambiguously mean
# "this isn't the real publisher page, it's a bot-check holding page".
_CAPTCHA_TITLE_MARKERS = (
    "just a moment",        # Cloudflare default
    "\u8bf7\u7a0d\u5019",   # "请稍候" (Cloudflare zh-CN)
    "checking your browser",  # Cloudflare + Sucuri
    "attention required",   # Cloudflare block page
    "verify you are human", # Cloudflare Turnstile
    "access denied",        # Akamai / generic WAF block
    "access to this page has been denied",  # PerimeterX
)
# URL markers (Cloudflare challenge iframe host).
_CAPTCHA_URL_MARKERS = (
    "cdn-cgi/challenge-platform",
    "/cdn-cgi/l/chk_jschl",
    "_cf_chl_opt",
)


@dataclass
class ProbeResult:
    publisher: str
    status: str
    detail: str = ""
    size_bytes: int = 0
    elapsed_s: float = 0.0
    meta: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status in PASSING_STATUSES

    def icon(self) -> str:
        return {
            STATUS_PDF_OK: "[PDF OK]",
            STATUS_AUTH_OK: "[AUTH OK]",
            STATUS_CAPTCHA: "[CAPTCHA]",
            STATUS_LOGIN_WALL: "[LOGIN]",
            STATUS_FIXTURE_BROKEN: "[FIXTURE]",
            STATUS_MOJIBAKE: "[MOJIBAKE]",
            STATUS_ERROR: "[ERROR]",
        }.get(self.status, "[?]")


def _probe_one(publisher: str, spec: dict, port: int, wait_s: float,
               verbose_log: Optional[Callable[[str], None]]) -> ProbeResult:
    """Probe a single publisher. See module docstring for status machine."""
    # Deferred import so `from cdp_login_probe import ProbeResult` in
    # test contexts doesn't force-load the whole pdf_downloader tree.
    from citationclaw.core.pdf_downloader import (
        _cdp_check_connection,
        _cdp_open_page,
        _cdp_close_page,
        _cdp_fetch_pdf_in_context,
        _cdp_evaluate,
        _pdf_bytes_are_mojibake,
        _pdf_title_matches,
    )

    def _vlog(msg: str):
        if verbose_log:
            verbose_log(msg)

    t0 = time.monotonic()
    if not _cdp_check_connection(port):
        return ProbeResult(publisher, STATUS_ERROR,
                           detail=f"port {port} not reachable",
                           elapsed_s=time.monotonic() - t0)

    page = None
    try:
        _vlog(f"  [{publisher}] opening landing {spec['landing_url']}")
        page = _cdp_open_page(port, spec["landing_url"])
        # Match harness's 8s wait; publishers run heavy SPA init +
        # PerimeterX/Akamai challenges (3-6s) before cookies settle.
        time.sleep(wait_s)
        ws = page.get("webSocketDebuggerUrl")
        if not ws:
            return ProbeResult(publisher, STATUS_ERROR,
                               detail="no webSocketDebuggerUrl",
                               elapsed_s=time.monotonic() - t0)

        location_after = ""
        doc_title = ""
        try:
            location_after = str(_cdp_evaluate(ws, "window.location.href", msg_id=60) or "")
            doc_title = str(_cdp_evaluate(ws, "document.title", msg_id=61) or "")
        except Exception:
            pass

        loc_lower = location_after.lower()
        title_lower = doc_title.lower()
        # CAPTCHA check MUST come before login / fixture_broken because a
        # Cloudflare challenge page can have a title that doesn't match any
        # of those but still indicates "this isn't the real publisher page
        # yet". Multi-mode harness run observed `title='请稍候…'` for
        # Elsevier probe -- was misreported as AUTH_OK, caller then burned
        # 120s in the real download path before giving up.
        looks_like_captcha = any(
            tok in title_lower for tok in _CAPTCHA_TITLE_MARKERS
        ) or any(
            tok in loc_lower for tok in _CAPTCHA_URL_MARKERS
        )
        looks_like_login = any(
            tok in loc_lower for tok in
            ("/login", "/signin", "/sign-in", "/authenticate",
             "accounts.", "/auth/")
        ) or any(
            tok in title_lower for tok in
            ("sign in", "signin", "log in", "login", "\u767b\u5f55")
        )
        looks_like_fixture_broken = any(
            tok in title_lower for tok in
            ("error: 404", "error 404", "not found", "page not found",
             "page unavailable", "unable to find", "does not exist")
        )

        _vlog(f"  [{publisher}] after wait: URL={location_after[:120]!r}")
        _vlog(f"  [{publisher}]             title={doc_title[:100]!r}")
        _vlog(f"  [{publisher}]             captcha={looks_like_captcha} "
              f"login_wall={looks_like_login} "
              f"fixture_broken={looks_like_fixture_broken}")

        if looks_like_captcha:
            return ProbeResult(publisher, STATUS_CAPTCHA,
                               detail=f"Cloudflare/Akamai challenge page "
                                      f"(title={doc_title[:70]!r}) -- the "
                                      f"real download path will block on "
                                      f"this until you solve the challenge "
                                      f"in the browser",
                               elapsed_s=time.monotonic() - t0,
                               meta={"location": location_after,
                                     "title": doc_title})

        if looks_like_login:
            return ProbeResult(publisher, STATUS_LOGIN_WALL,
                               detail=f"URL redirected to auth "
                                      f"(location={location_after[:80]!r})",
                               elapsed_s=time.monotonic() - t0,
                               meta={"location": location_after,
                                     "title": doc_title})

        if looks_like_fixture_broken:
            return ProbeResult(publisher, STATUS_FIXTURE_BROKEN,
                               detail=f"landing is 404/error (title="
                                      f"{doc_title[:70]!r}) -- probe DOI needs "
                                      f"update, NOT an auth issue",
                               elapsed_s=time.monotonic() - t0,
                               meta={"location": location_after,
                                     "title": doc_title})

        # Landing loaded cleanly. Try the PDF fetch as a bonus check.
        _vlog(f"  [{publisher}] fetching PDF {spec['pdf_url']}")
        data = _cdp_fetch_pdf_in_context(ws, spec["pdf_url"])
        elapsed = time.monotonic() - t0

        if data is None:
            return ProbeResult(publisher, STATUS_AUTH_OK,
                               detail=f"landing loaded (title={doc_title[:50]!r}) "
                                      f"-- PDF direct fetch failed (expected "
                                      f"for {publisher}; harness uses a "
                                      f"publisher-specific flow)",
                               elapsed_s=elapsed,
                               meta={"location": location_after,
                                     "title": doc_title,
                                     "pdf_url": spec["pdf_url"]})

        if len(data) < 1000:
            return ProbeResult(publisher, STATUS_AUTH_OK,
                               detail=f"landing OK, PDF endpoint returned "
                                      f"tiny {len(data)}B (probably error page)",
                               size_bytes=len(data),
                               elapsed_s=elapsed)

        if _pdf_bytes_are_mojibake(data):
            return ProbeResult(publisher, STATUS_MOJIBAKE,
                               detail="content streams corrupt "
                                      "(ScraperAPI-style byte mangling)",
                               size_bytes=len(data),
                               elapsed_s=elapsed)

        title_match_note = ""
        try:
            matched = _pdf_title_matches(data, spec["title"], threshold=0.25)
            title_match_note = (" title-match: yes" if matched else
                                " title-match: no (first page may be "
                                "cover/ToC; fixture title might need update)")
        except Exception:
            title_match_note = " title-match: skipped (PyMuPDF unavailable)"

        return ProbeResult(publisher, STATUS_PDF_OK,
                           detail=f"{len(data)//1024} KB{title_match_note}",
                           size_bytes=len(data),
                           elapsed_s=elapsed)
    except Exception as e:
        return ProbeResult(publisher, STATUS_ERROR,
                           detail=f"{type(e).__name__}: {e}",
                           elapsed_s=time.monotonic() - t0)
    finally:
        if page:
            try:
                _cdp_close_page(port, page.get("id", ""))
            except Exception:
                pass


def probe_all(port: int, publishers: Optional[list] = None,
              wait_s: float = 8.0,
              verbose_log: Optional[Callable[[str], None]] = None) -> list:
    """Run probes for each publisher sequentially; return list of ProbeResult.

    Args:
        port: Chrome/Edge remote debugging port. Must already be live.
        publishers: list of publisher keys (subset of PUBLISHER_PROBES).
                    None -> all 5 default publishers.
        wait_s: seconds to wait after opening landing tab. Default 8s
                (matches pdf_downloader._try_cdp_ieee).
        verbose_log: optional callable(str) for per-step diagnostic lines.
                     None -> silent. Pass `print` for CLI, pass
                     `self.log_manager.info` for pipeline integration.

    Returns:
        list of ProbeResult, one per publisher. Never raises -- errors
        become STATUS_ERROR results so callers can tabulate results
        cleanly.
    """
    if publishers is None:
        publishers = list(PUBLISHER_PROBES.keys())
    unknown = [p for p in publishers if p not in PUBLISHER_PROBES]
    if unknown:
        raise ValueError(f"unknown publisher(s): {unknown}. "
                         f"valid: {list(PUBLISHER_PROBES.keys())}")

    results = []
    for pub in publishers:
        spec = PUBLISHER_PROBES[pub]
        r = _probe_one(pub, spec, port, wait_s, verbose_log)
        results.append(r)
    return results


def format_summary(results: list) -> str:
    """One-line roll-up for log output, e.g. 'PDF_OK:2, AUTH_OK:3'."""
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return ", ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
