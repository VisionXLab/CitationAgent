# CLAUDE.md -- CitationClaw v2 Development Context

> This file is read by Claude Code at session start. Keep it up to date.

---

## Project Overview

**CitationClaw** is an academic citation analysis tool with a 5-phase pipeline:

- Phase 1: Google Scholar citation scraping (scraper.py, url_finder.py)
- Phase 2: Metadata collection + PDF download (metadata_collector.py, pdf_downloader.py)
- Phase 3: Scholar assessment + export (scholar_assess, exporter)
- Phase 4: Citation context extraction (phase4_citation_extract.py, pdf_parser.py)
- Phase 5: Dashboard report generation (dashboard_generator.py)

### Key Files Map

| File | Role |
|------|------|
| core/pdf_downloader.py | **Main target** - 12-tier cascade PDF download engine |
| core/scraper.py | ScraperAPI integration for Google Scholar (reference pattern) |
| core/http_utils.py | Shared httpx client factory with proxy detection |
| core/browser_manager.py | Playwright browser pool (only used for GS search, not PDF) |
| core/metadata_collector.py | S2 -> OpenAlex -> arXiv metadata aggregation |
| core/s2_client.py | Semantic Scholar API client |
| core/openalex_client.py | OpenAlex API client (free, no key) |
| core/arxiv_client.py | arXiv API client |
| core/pdf_mineru_parser.py | MinerU 4-tier PDF parsing (Cloud Agent -> Precision -> Local -> PyMuPDF) |
| core/pdf_parser.py | Citation context extraction from parsed text |
| core/url_finder.py | Google Scholar paper URL finder via ScraperAPI |
| skills/phase4_citation_extract.py | Orchestrates PDF download -> parse -> LLM dual-agent extraction |
| app/config_manager.py | AppConfig pydantic model, all config fields |
| config/providers.yaml | LLM provider presets (V-API = api.gpt.ge) |
| config/rules/data_sources.yaml | Metadata source priority config |

### Available Resources

| Resource | Type | Status |
|----------|------|--------|
| ScraperAPI | Web scraping proxy with JS render | Integrated, under-leveraged for PDF |
| V-API (api.gpt.ge) | LLM proxy (Gemini, etc.) | Integrated, default provider |
| MinerU API | PDF parsing cloud service | Well-integrated (4-tier) |

---

## Current Task: Improve PDF Download Success Rate

Goal: Break through IEEE / Springer / Elsevier restrictions.

Analysis Date: 2026-04-03

---

## Architecture Analysis

### Current PDF Download Cascade (pdf_downloader.py)

| # | Source | Scenario | Status |
|---|--------|----------|--------|
| 0 | Cache hit | All | OK |
| 1 | GS sidebar PDF link | GS-provided PDFs | OK |
| 2 | OpenAlex OA PDF | Open Access papers | OK |
| 3 | CVF open access | CVPR/ICCV/WACV | OK |
| 4 | S2 openAccessPdf | Non-arxiv direct links | OK |
| 5 | S2 API re-lookup | Papers with s2_id | OK |
| 6 | DBLP conference lookup | NeurIPS/ICML/ICLR/AAAI | OK |
| 7 | Sci-Hub (3 mirrors) | Papers with DOI | UNSTABLE |
| 8 | arXiv PDF | Papers with arxiv_id | OK |
| 9 | GS paper_link + URL transform | IEEE/Springer/ACL etc. | BROKEN |
| 10 | curl + socks5 + Chrome Cookie | IEEE/Springer/Elsevier | BROKEN |
| 11 | DOI redirect + Cookie | Papers with DOI | BROKEN |
| 12 | Unpaywall | Papers with DOI | TOO LATE |
| 13 | ScraperAPI + LLM (last resort) | All publisher pages | UNDER-LEVERAGED |

### Root Causes of Failure

**IEEE** (ieeexplore.ieee.org):
- stamp.jsp is JS-rendered, httpx gets empty HTML
- Cloudflare WAF + Akamai Bot Manager blocks simple HTTP
- _detect_chrome_profile() hardcoded to macOS path (Windows broken)
- 3-hop flow (abstract -> stamp -> getPDF -> iel7/*.pdf) fragile

**Springer** (link.springer.com):
- /article/DOI -> /content/pdf/DOI.pdf correct but non-OA returns 403
- No institutional IP authentication
- Springer SharedIt links (rdcu.be) not utilized

**Elsevier** (www.sciencedirect.com):
- /pii/XXX -> /pii/XXX/pdfft needs session + CSRF token
- React SPA, citation_pdf_url meta may not exist
- PerimeterX bot detection blocks httpx

**Cross-cutting**:
- Chrome Cookie extraction macOS only
- SOCKS5 depends on curl, unreliable on Windows
- Free OA sources placed too late in chain
- ScraperAPI (strongest tool) used only as last resort

### ScraperAPI: Current vs. Potential

| Feature | Credits | Bypasses | Used for PDF? |
|---------|---------|----------|---------------|
| Standard | 1 | Basic IP blocks | NO |
| render=true | 10 | JS pages | Only in #13 |
| premium=true | 10 | Residential IPs | NO |
| ultra_premium=true | 75 | Cloudflare/Akamai/PX | NO |
| country_code | +0 | Geo-restrictions | Only in #13 |
| session_number | +0 | Cookie persistence | NO |

Key insight: ScraperAPI is battle-tested for Cloudflare bypass in GS scraping
but only used as last option for PDF downloads with minimal flags.

### V-API Potential

- gemini-3-flash-preview-search has Google Search grounding
- Can search for alternative PDF sources (author pages, ResearchGate, repos)
- Current _llm_find_pdf_link() only analyzes fetched HTML, no web search
- New channel: _llm_search_alternative_pdf()

### MinerU Strategic Impact

- High-quality parsing means any version (preprint, draft) works
- Changes problem from get publisher PDF to get any version
- OCR support makes older scanned papers usable

---

## Implementation Plan

### Phase A: ScraperAPI Publisher Channel (highest ROI) -- IMPLEMENTED

Create _scraper_publisher_download() with publisher-specific profiles:
- IEEE: ultra_premium + render + session (3-hop stamp flow)
- Springer: premium + render (needs residential IP)
- Elsevier: ultra_premium + render + session (PerimeterX bypass)

Move from position #13 to #9 in chain. Cost: ~75-150 credits/paper.

### Phase B: V-API Search-Powered Fallback -- IMPLEMENTED

Added _llm_search_alternative_pdf() using search-grounded model (gemini-3-flash-preview-search).
Finds preprints, author copies, repository versions when publisher PDFs are blocked.
Position #12 in cascade (after ScraperAPI publisher, before curl).
Timeout: 90s (search models need time for web search). Tested: Elsevier paper downloaded via arXiv preprint.

### Phase C: Priority Chain Rebalancing -- PLANNED

1. Move Unpaywall from #12 to #2
2. Add CORE.ac.uk API
3. Restructure: Free OA -> ScraperAPI Publisher -> Last resorts

### Expected Impact

| Publisher | Before | After A | After A+B+C |
|-----------|--------|---------|-------------|
| IEEE | ~15-20% | ~70-80% | ~85-90% |
| Springer | ~20-25% | ~60-70% | ~80-85% |
| Elsevier | ~10-15% | ~65-75% | ~80-85% |
| OA papers | ~80-90% | ~80-90% | ~90-95% |

---

## Dev Log

### 2026-04-03 -- Initial Analysis

- Session goal: Analyze project architecture, identify PDF download bottlenecks
- Analyzed files: pdf_downloader.py, scraper.py, http_utils.py, browser_manager.py,
  metadata_collector.py, s2_client.py, openalex_client.py, arxiv_client.py,
  pdf_mineru_parser.py, phase4_citation_extract.py, config_manager.py, providers.yaml,
  data_sources.yaml, search_strategy.yaml, test_pdf_downloader.py
- Findings:
  - 12-tier cascade well-designed but publisher tiers (#9-#13) are weak
  - ScraperAPI strongest tool but placed last with minimal flags
  - Windows compatibility broken (macOS-only Chrome cookie path)
  - V-API search-grounded models could discover alternative PDF sources
  - MinerU tolerance means preprints acceptable (reframes the problem)
- Decision: Prioritize Phase A (ScraperAPI publisher channel) as highest ROI
- Next step: Start coding Phase A

### 2026-04-03 -- Phase A Implementation: ScraperAPI Publisher Channel

- **Changes to `core/pdf_downloader.py`**:
  - Added `_detect_publisher(url)` — detects IEEE/Springer/Elsevier/ACM/Wiley from URL
  - Added `_publisher_from_doi(doi)` — detects publisher from DOI prefix (10.1109=IEEE, etc.)
  - Added `_SCRAPER_PUBLISHER_PROFILES` — per-publisher ScraperAPI config:
    - IEEE: ultra_premium + render + session (Cloudflare + Akamai bypass)
    - Elsevier: ultra_premium + render + session (PerimeterX bypass)
    - Springer: premium + render (lighter protection)
    - ACM/Wiley: premium/ultra_premium + render
  - Added `_scraper_build_url()` — builds ScraperAPI URL with profile params
  - Added `_scraper_publisher_download()` — main new method:
    - Smart URL transform before sending to ScraperAPI
    - Publisher-specific PDF extraction after JS render
    - Session persistence for multi-hop flows (IEEE stamp chain)
    - LLM fallback for stubborn pages
    - 3rd-hop support for IEEE (stamp -> getPDF -> iel7/*.pdf)
  - Added `_extract_ieee_pdf()` — IEEE-specific: pdfUrl JSON, iframe/embed, iel7 links, arnumber
  - Added `_extract_elsevier_pdf()` — ScienceDirect: React state pdfLink, pdfft construction
  - Added `_extract_springer_pdf()` — Springer: citation_pdf_url, content/pdf link, DOI construction
  - **Reordered download cascade**:
    - Unpaywall moved from #12 to #1 (right after GS sidebar)
    - ScraperAPI publisher moved from #13 to #11 (after GS link transform, before curl)
    - ScraperAPI smart fallback (#13) now only for non-publisher pages
    - DOI redirect moved before ScraperAPI publisher (cheap attempt first)
- **Tests**: 31 new tests added (45 total, all passing)
  - TestDetectPublisher (7 tests), TestPublisherFromDoi (7 tests)
  - TestPublisherProfiles (4 tests), TestScraperBuildUrl (3 tests)
  - TestExtractIeeePdf (4 tests), TestExtractElsevierPdf (3 tests)
  - TestExtractSpringerPdf (3 tests)
- Full suite: 110 passed, 2 failed (pre-existing: openai module missing, cache bug)
- **Phase A status**: COMPLETE
- **Bug fix**: Windows GBK encoding crash on U+2713 check mark in log messages
  - `[PDF✓]` changed to `[PDF OK]` + UnicodeEncodeError fallback in `_ok()`
  - This bug was making ALL downloads appear as failures on Windows

### 2026-04-04 -- Live Test Results (Free Sources, No ScraperAPI)

Test: 7 real papers, no ScraperAPI key, free sources only.

| Paper | Publisher | Result | Source | Time |
|-------|-----------|--------|--------|------|
| NumPy (Nature) | OA | OK 1189KB | Unpaywall | 12.8s |
| Attention Is All You Need | arXiv | OK 2163KB | arXiv | 23.0s |
| MAE (IEEE TPAMI) | IEEE | OK 10803KB | Sci-Hub | 37.3s |
| Faster R-CNN (Springer IJCV) | Springer | OK 15993KB | Sci-Hub | 114.9s |
| Object Detection Survey (Elsevier PR) | Elsevier | FAIL | - | 30.9s |
| BERT (ACL Anthology) | OA | OK 767KB | GS link transform | 26.8s |
| ResNet (CVPR) | IEEE/CVF | OK 280KB | Sci-Hub | 16.4s |

**Result: 6/7 (86%)** without ScraperAPI.

Elsevier failure diagnosis:
- Unpaywall: 404 (paper not OA)
- Sci-Hub: 2 mirrors unreachable, 1 returns 404
- URL transform (pdfft): HTTP 403 (PerimeterX blocks)
- DOI redirect: 404 (proxy routing issue)
- **This is the exact scenario for Phase A ScraperAPI publisher channel**

Key insight: Unpaywall move to #1 already paid off (NumPy paper).
Cascade reorder working correctly.

### 2026-04-04 -- Live Test with ScraperAPI Key

ScraperAPI key: a42143ef... (plan: standard, 100k credits, 20 concurrent)

| Paper | Publisher | Result | Source | Time |
|-------|-----------|--------|--------|------|
| ObjDetSurvey | Elsevier | FAIL | ScraperAPI 500 | 81.4s |
| MAE | IEEE | OK 7271KB | DBLP | 7.6s |
| Faster R-CNN | Springer | OK 15993KB | Sci-Hub | 26.5s |
| YOLOv4 | Elsevier | OK 3847KB | DBLP | 8.7s |
| GNN Survey | ACM | OK 1332KB | DBLP | 8.6s |
| BatchNorm | Wiley | OK 5893KB | Sci-Hub | 21.4s |
| NumPy | OA | OK 1189KB | Unpaywall | 5.6s |
| Attention | arXiv | OK 2163KB | arXiv | 24.8s |

**Result: 7/8 (88%)** — only stubborn Elsevier fails.

Elsevier ScraperAPI findings:
- render=true causes HTTP 500 on ScienceDirect (PerimeterX too aggressive)
- premium+us returns 200 but **wrong page** (proxy cache/routing issue)
- DOI redirect through ScraperAPI returns 404
- ultra_premium not available on this plan
- Elsevier profile downgraded from ultra_premium to premium+us
- **Elsevier needs Phase B (V-API search) or ultra_premium plan**

Windows bug fix: [PDF checkmark] changed to [PDF OK] to avoid GBK UnicodeEncodeError.
ScraperAPI render fix: render article page, not pdfft download URL.
- **Next step**: Phase B (V-API search-powered fallback) for Elsevier

### 2026-04-04 -- Smoke Test: Found and Fixed Wrong-PDF Bug

Smoke test: real pipeline flow (metadata -> PDF download -> parse) for "Attention Is All You Need".

**Bug found**: OpenAlex returned wrong OA PDF URL (Japanese plasma physics paper instead of Transformer paper).
The cascade accepted it because it passed `b"%PDF-"` check — it IS a valid PDF, just the wrong one.

**Fix**: Added `_pdf_title_matches()` verification guard in `_ok()`:
- Extracts first-page text via PyMuPDF (fast, in-memory, no full parse)
- Word-overlap check against expected title (threshold 0.4)
- Trusted sources (arXiv by ID, Sci-Hub by DOI) skip verification
- Fails gracefully: if PyMuPDF missing or extraction fails, accepts the PDF

**Smoke test result after fix**:
```
[PDF SKIP] OpenAlex OA PDF - title mismatch, skipped    ← wrong PDF blocked
[PDF OK] arXiv (2163KB)                                  ← correct PDF downloaded
Content verification: CORRECT PAPER
```

Tests: 52 passed (7 new title verification tests)

### 2026-04-04 -- Phase B Implementation: LLM Search-Powered Fallback

- Added `_llm_search_alternative_pdf()` to pdf_downloader.py:
  - Uses search-grounded model (gemini-3-flash-preview-search via V-API)
  - Prompt asks LLM to search for arXiv, author homepage, repo, ResearchGate versions
  - Filters out publisher/DOI URLs (only returns free sources)
  - Tries top 5 candidate URLs, downloads first valid PDF
  - 90s timeout for search-grounded models (they search the web)
- Added to cascade as #12 (after ScraperAPI publisher, before curl)
- Added "llm_search" source label

**Live test result**: The Elsevier paper that failed ALL other methods:
```
ScraperAPI Elsevier → failed
LLM Search → found 5 URLs → arxiv.org/pdf/1807.05511.pdf → 3774KB
Content verified: "object detection" on first page
```

IP condition investigation (pre-Phase B):
- Tested each source with and without VPN proxy
- Finding: 0 sources break without proxy for reachability
- BUT: Unpaywall OA PDFs (nature.com etc.) fail without good IP
- Core bottleneck: JS rendering + paywalls, not IP
- Browser-based approaches (Playwright, CDP) all blocked by Cloudflare/PerimeterX
- Phase B (LLM search) solves this by finding alternative versions that work from ANY IP

### 2026-04-04 -- 100-Paper Benchmark Results

Benchmark: 100 well-known ML/CV/NLP papers across 8 categories.
Both versions tested with clean caches, same papers, same network conditions.

**Raw download count**:
- Baseline (original): 97/100
- Improved (ours): 96/100 (4 transient failures, all pass on retry)

**Content verification** (PyMuPDF first-page title check on baseline PDFs):
- Baseline: 22 out of 32 checked PDFs were WRONG PAPER (same plasma physics PDF)
- Root cause: OpenAlex returned wrong OA PDF URL, baseline accepted without verification

**TRUE correct-paper rate**:

| Metric | Baseline | Improved |
|--------|----------|----------|
| Correct PDFs | 75/100 (75%) | 96-100/100 (96-100%) |
| Wrong PDFs | 22 | 0 |
| Structural fails | 3 | 0 |
| Transient fails | 0 | 4 (pass on retry) |

Per-category:

| Category | Baseline (correct) | Improved |
|----------|-------------------|----------|
| A_arxiv (20) | ~14/20 | 20/20 |
| B_ieee (15) | ~14/15 | 13/15 |
| C_springer (10) | ~10/10 | 10/10 |
| D_elsevier (10) | ~7/10 | 9/10 |
| E_acm (10) | ~7/10 | 9/10 |
| F_open_access (15) | ~8/15 | 15/15 |
| G_conference_oa (10) | ~5/10 | 10/10 |
| H_edge (10) | ~7/10 | 10/10 |

Source distribution (improved):
- arXiv: 51, DBLP: 26, LLM search: 8, Sci-Hub: 6, Unpaywall: 4, S2: 1

Key improvements that drove the +21pp gain:
1. OpenAlex title guard (openalex_client.py) — stopped 22 wrong-paper downloads
2. PDF content verification (_pdf_title_matches) — safety net for any source
3. Unpaywall moved to #1 — fast OA discovery
4. LLM search Phase B — rescued 8 papers no other source found
5. Cascade reorder — arXiv/DBLP dominate (77/96) vs broken OpenAlex (23/97)

---

## Conventions

- Log messages: Chinese with English technical terms
- Source labels: _SOURCE_LABELS dict in pdf_downloader.py
- PDF validity: `data[:5] == b"%PDF-"` and `len(data) > 1000`
- Cache key: MD5(DOI or title) -> {hash}.pdf in data/cache/pdf_cache/
- Config: pydantic AppConfig in config_manager.py
- Tests: test/ directory, pytest
