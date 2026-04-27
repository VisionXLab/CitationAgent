# CLAUDE.md Archive -- CitationClaw v2

Archived 2026-04-20 from `CLAUDE.md` to keep the main
context file under 1200 lines. Contains dev-log entries
older than one week (2026-04-03 and 2026-04-04). Read-only
historical record; new entries still go to `CLAUDE.md`.

---

## Dev Log (archived)

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


---

## Dev Log (archived 2026-04-21)

The following entries (2026-04-13, 2026-04-14, 2026-04-18) were
moved here from `CLAUDE.md` on 2026-04-21 because the live file
had grown past ~1500 lines. 2026-04-19 and newer entries remain
in the live file.

### 2026-04-13 -- Session: End-to-End Pipeline Testing & Download Reliability

#### test_papers.py Rewrite

Rewrote `test_papers.py` from bare-title-only to full Phase 1 → Phase 2 simulation:

- **Before**: Bare titles with `paper_link=""`, `gs_pdf_link=""` — tiers #0 (GS sidebar), #10 (GS link transform), #11 (ScraperAPI publisher), #12 (CDP) never exercised
- **After**: Full pipeline — `PaperURLFinder` finds GS `cites=` URL → `GoogleScholarScraper` scrapes citing papers with all GS data → Phase 2 downloads with real `paper_link`/`gs_pdf_link`/`authors_raw`
- CLI: `--bare` (old behavior), `--pages N`, `--limit N`, `--skip-parse`
- Summary: source distribution, GS utilization stats

#### Bug Fixes

1. **LLM search 429 rate-limit auto-disable** (`pdf_downloader.py`)
   - Before: Only 401/403 auto-disabled; 429 kept retrying (~90s wasted)
   - After: 429 added to auto-disable, first hit kills LLM search for the run

2. **LLM search wrong-paper acceptance** (`pdf_downloader.py`)
   - Before: Returned first valid PDF without title check; remaining candidates never tried
   - After: `_pdf_title_matches()` inside download loop; wrong paper → skip → next URL
   - Triggered by: DiffHarmony++ getting wrong OpenReview ID (`yI0Xv6K4fS` vs correct `FRUgSgnASr`)

3. **LLM search model mismatch** (`task_executor.py`)
   - Before: Passed `dashboard_model` (gemini-nothinking, no web search) to PDFDownloader
   - After: Passes `config.openai_model` (search-grounded model)
   - Root cause: `llm_model=getattr(config, 'dashboard_model', '') or config.openai_model` — dashboard_model was set, overriding the search model

4. **GS sidebar PDF silent failure** (`pdf_downloader.py`)
   - Added log: `[GS PDF] 非PDF内容, 跳过` when tier #0 gets HTML instead of PDF

5. **Config overwrite by web UI** (`config.json`)
   - Web UI saves in-memory config on every settings change, overwriting manual edits
   - `cdp_debug_port: 9222` was reverted to `0` by the UI
   - Workaround: must restart app after manual config.json edits

#### New Download Sources

1. **figshare URL transform + HTML extraction** (`pdf_downloader.py`)
   - `_transform_url()`: `/articles/TYPE/TITLE/ID` → `/ndownloader/articles/ID/versions/1`
   - `_extract_pdf_url_from_html()`: figshare `ndownloader/files/`, `data-file-id` patterns
   - GS sidebar often links to figshare landing pages (HTML, not PDF)

2. **arXiv title search — Tier #8b** (`pdf_downloader.py`)
   - `_search_arxiv_by_title()`: searches `export.arxiv.org/api/query?search_query=ti:TITLE`
   - Runs when metadata has no `arxiv_id` — many papers have arXiv preprints that S2/OpenAlex don't index
   - Free, ~0.3s, word-overlap title matching (≥0.7 threshold)

3. **OpenReview title search — Tier #8c** (`pdf_downloader.py`)
   - `_search_openreview()`: searches OpenReview v2 API by title
   - Falls back to ScraperAPI when Cloudflare-blocked (common from China)
   - Verified: correctly finds DiffHarmony++ at `openreview.net/pdf?id=mWlfCKgtks`
   - Source label: `openreview`

#### Persistent File Logging (`log_manager.py`, `task_executor.py`)

- **Before**: Logs only to stdout + WebSocket (in-memory, max 1000 lines, lost on close)
- **After**: Every run creates `run.log` in result directory
- `set_log_file(path)` / `close_log_file()` / `_write_to_file()` in LogManager
- Line-buffered UTF-8, format: `[HH:MM:SS] [LEVEL] message`
- Wired into all 3 task entry points, closed in `finally` blocks
- Path: `data/result-TIMESTAMP/run.log`

#### CDP Browser Improvements (`pdf_downloader.py`)

1. **Proxy bypass for campus network auth**
   - Added `--proxy-bypass-list=ieeexplore.ieee.org;sciencedirect.com;...` to browser launch
   - Problem: FLClash system proxy makes IEEE see proxy IP, not campus IP
   - Fix: Publisher domains bypass proxy → campus IP visible → institutional auth works

2. **Chrome priority on Windows**
   - Moved Chrome before Edge in `browser_paths` list (user preference)

3. **LLM search disable flag**
   - Added `disable_llm_search` constructor parameter to PDFDownloader
   - `task_executor.py` sets `disable_llm_search=True` (V-API key has transient 401s)
   - TODO: re-enable once V-API stabilizes

4. **CDP status logging**
   - Logs `[PDF下载] CDP端口: 9222, LLM搜索: 禁用` at download start

#### Dependencies Added

- `websocket-client` 1.9.0 — required for CDP browser communication

#### Config Changes (`config.json`)

- `cdp_debug_port`: `0` → `9222`

#### Real-World Test: 14 Citing Papers for "Image harmonization by matching regional references"

- Result: 11/14 downloaded (cache hit for most)
- 3 failures diagnosed:
  - **"An unsupervised transfer method..."**: Has arXiv ID `1912.05189`, downloads fine in isolation. Pipeline failure was transient — LLM 401 consumed all retries. Fixed by disabling LLM search.
  - **"BSTNet" (IEEE CCDC 2024)**: Paywall-only. No arXiv, Sci-Hub doesn't have it, no OA. ScraperAPI returns 500 on IEEE. Needs CDP + campus network.
  - **"Image Harmonization Algorithm" (IEEE CVAA 2025)**: Same — brand new 2025 IEEE conference paper, paywall-only. Needs CDP + campus network.

#### Updated Download Cascade (19 tiers)

| # | Source | New? |
|---|--------|------|
| 0 | Cache | |
| 1 | GS sidebar PDF | |
| 2 | Unpaywall | |
| 3 | OpenAlex OA PDF | |
| 4 | CVF open access | |
| 5 | S2 openAccessPdf | |
| 6 | S2 API re-lookup | |
| 7 | DBLP conference | |
| 8 | Sci-Hub | |
| 9 | arXiv (by ID) | |
| 9b | arXiv title search | **NEW** |
| 9c | OpenReview title search | **NEW** |
| 10 | GS link + URL transform | |
| 11 | ScraperAPI publisher | |
| 12 | CDP browser session | |
| 13 | LLM search | (disabled) |
| 14 | curl + socks5 | |
| 15 | DOI redirect | |
| 16 | ScraperAPI + LLM fallback | |

### 2026-04-14 -- Fix: OpenReview dead revision IDs

**Problem**: `_search_openreview()` returned the first title-matched forum ID.
OpenReview stores multiple notes per paper (submission, revision, camera-ready).
Some old revision IDs (e.g. `mWlfCKgtks`) return 404 on `/pdf?id=`, while the
correct ID (`FRUgSgnASr`) works. The code picked the first match and failed.

Additionally, openreview.net is Cloudflare-blocked from China. Direct httpx
download fails. Must route PDF download through ScraperAPI.

**Fix** (`pdf_downloader.py`):
1. `_search_openreview()` now returns `List[str]` (all matching forum IDs, deduplicated) instead of `Optional[str]`
2. Tier #8c caller loops through each candidate URL
3. Each candidate is tried via ScraperAPI first, then direct fallback
4. Logs each attempt: `[OpenReview] 尝试: https://openreview.net/pdf?id=XXX`

**Verified**: DiffHarmony++ — first candidate `mWlfCKgtks` → 404, second `FRUgSgnASr` → 7147KB PDF success.

### 2026-04-18 -- Added dev-history-sync Claude Code Skill

- **New file** `.claude/skills/dev-history-sync/SKILL.md` (at workspace root
  `D:/PROJECT/citationclaw/.claude/skills/`):
  - Project-local Claude Code skill that auto-appends Dev Log entries to both
    this file and the workspace-root `CLAUDE.md` after any code/config/test change.
  - Spells out when to invoke vs skip (read-only sessions, meta-edits excluded).
  - Defines entry format matching existing convention: date heading, per-file
    bullets, Tests / Findings / Status / Next step sections.
  - ASCII-only rule to avoid the Windows GBK unicode crash already documented
    in the 2026-04-03 Phase A entry.
  - Provides `Edit`-tool recipe: anchor on `\n---\n\n## Conventions\n` and
    append above it, so both CLAUDE.md copies stay in sync.
- **Changes to `CLAUDE.md`** (this file + root copy):
  - Added Conventions bullet pointing at the skill so future Claude Code
    sessions discover and use it without being told.
- **Rationale**: Dev Log had been maintained by hand; risk of the narrative
  drifting out of sync with actual code state. Formalizing the protocol as a
  skill keeps history exhaustive with minimal assistant overhead.
- **Note on divergence**: root `CLAUDE.md` is currently missing the 2026-04-13
  and 2026-04-14 entries present only in this file. Skill will write to both
  going forward but will **not** auto-merge pre-existing divergence.
- **Status**: COMPLETE
- **Next step**: Use the skill on the next real code change; tune templates if
  the appended entries feel noisy.

### 2026-04-18 -- New sibling tool: eval_toolkit/phase12_harness/ (Phase 1+2+3 dev-loop harness)

Not a change to this repo's code -- sibling directory `eval_toolkit/phase12_harness/`
(at `D:/PROJECT/citationclaw/eval_toolkit/`) was added as a dev-loop harness
that drives this repo's `TaskExecutor._run_new_phase2_and_3`.

- **What the harness does**:
  - Reads an existing Phase 1 output (`paper1_citing.jsonl`) per target paper
  - Validates the Phase 1 -> Phase 2 data contract (7 failure modes F1-F7
    derived from `PipelineAdapter.flatten_phase1_line`)
  - Invokes `TaskExecutor._run_new_phase2_and_3` directly (real production
    code path, not a copy -- so schema drift is caught for free)
  - Captures per-step diagnostics: metadata source mix, self-cite detection,
    PDF download rate + tier, PDF-author cross-validate, Phase 3 scholar tiers
  - Produces JSON / Markdown / HTML reports with a 6-dim health score
- **Impact on this repo**: none direct. But using the harness repeatedly
  while editing `core/metadata_collector.py`, `core/pdf_downloader.py`,
  `core/pipeline_adapter.py`, and `app/task_executor.py` will surface
  regressions before they hit production.
- **Entry point**: `python eval_toolkit/phase12_harness/cli.py --contract-only`
  from the workspace root -- uses this repo's `.venv/Scripts/python.exe`.
  See `eval_toolkit/phase12_harness/README.md` for full usage + GT explanation.
- **Fixtures**: reads 14 target paper folders under
  `D:/PROJECT/citationclaw/\u6797\u94ee\u8001\u5e08\u8bba\u6587\u88ab\u5f15\u5206\u6790/`
  (each has `paper1_citing.jsonl`, `merged_authors.jsonl`, `test_results.*`).
  Total 2182 citing papers across all targets. The "Image harmonization by
  matching regional references" target has 14 citing papers -- good for
  single-target smoke testing.
- **Status**: harness COMPLETE (MVP); this repo UNCHANGED by the addition.
- **Bug-catch potential**: if this repo's Phase 1 output ever drops the
  `authors` dict or renames `gs_pdf_link`, the harness's contract check
  (F4 / UNKNOWN) will immediately flag it.

### 2026-04-18 -- Harness first run surfaced 2 latent production bugs

Running `python eval_toolkit/phase12_harness/cli.py --grep "Image harmonization"`
completed Phase 2 cleanly (11/14 PDFs, 53 authors enriched) but crashed in
Phase 3 scholar search after 663s, revealing two real bugs in this repo:

1. **Missing `import asyncio` in `core/scholar_search_agent.py`** (line 136):
   - Code: `except asyncio.TimeoutError:` in `search_paper_authors()`
   - `asyncio` is not imported at module top (only used inside `_aio` alias)
   - `NameError: name 'asyncio' is not defined`
   - Latent: only triggers on timeout / 401; unit tests don't exercise timeout path
   - Fix: add `import asyncio` at top of `scholar_search_agent.py`

2. **Windows GBK unicode crash in `app/log_manager.py`** (line 119):
   - `print(f"[{ts}] [{level}] {message}")` uses sys.stdout which is `cp936` (GBK) on Windows by default
   - Crash when `message` contains `\u26a0` (warning triangle, used in task_executor.py
     line 783 as `"\u26a0 \u5931\u8d25"`)
   - Same bug class as the 2026-04-03 `[PDF\u2713]` -> `[PDF OK]` fix, but warning
     triangles were missed
   - Propagated: the `UnicodeEncodeError` wasn't caught, so the entire Phase 3
     gather() task crashed, aborting the pipeline before merged_authors.jsonl
     was written
   - Fix: either (a) wrap `print(...)` in try/except UnicodeEncodeError like the
     existing `_write_to_file`, or (b) force `sys.stdout.reconfigure(encoding='utf-8',
     errors='replace')` at startup, or (c) replace all warning triangles with
     ASCII `[WARN]`

3. **V-API key 401 was the underlying trigger**: `openai.AuthenticationError:
   Error code: 401 - {'message': '\u65e0\u6548\u7684\u4ee4\u724c'}`. Consistent
   with CLAUDE.md 2026-04-13 entry `disable_llm_search=True`; this entry
   confirms the same key also fails for Phase 3 scholar search. Not a code bug,
   but a reminder the V-API key needs rotation.

- **Harness value validation**: exactly the bug-class the harness was built
  to catch -- all three issues would have been invisible in the old
  `test_papers.py` flow (which skips Phase 3) and bare-title benchmarks.
- **Harness report scoring fix**: crashed runs previously got inflated scores
  (e.g. 60/C) because zero-data dimensions defaulted to 100. Fixed in
  `eval_toolkit/phase12_harness/report.py` -- `data_missing=True` branch now
  scores all downstream dimensions as 0, so this run correctly receives 20/F
  with only Contract (20%) contributing.
- **Status**: bugs 1 & 2 still OPEN in this repo; harness already records them
  in `harness_error.txt` per target for easy triage.
- **Next step**: add `import asyncio` + GBK-safe log_manager.print; re-run
  harness to verify Phase 3 completes, measure baseline Health score.

### 2026-04-18 -- Fix: bugs 1 & 2 surfaced by harness

- **Bug 1 fix: `core/scholar_search_agent.py`**
  - Added `import asyncio` at module top (line 9).
  - Removed local `import asyncio as _aio` inside `search_paper_authors()`.
  - `asyncio.wait_for(...)` / `except asyncio.TimeoutError:` now reference the
    module-level name consistently.
  - Replaced `\u26a0` (warning triangle) in log messages with ASCII `[WARN]`
    in-file (defense in depth; also relies on Bug 2 fix below).
- **Bug 2 fix: `app/log_manager.py`**
  - Added `import sys` + module-level helper `_best_effort_utf8_console()`
    that calls `sys.stdout.reconfigure(encoding='utf-8', errors='replace')`
    (same for stderr) at import time. Silently no-ops on non-reconfigurable
    streams so it can't regress anything.
  - Wrapped the `_log()` method's `print(...)` in try/except
    `UnicodeEncodeError`: on crash, re-encodes via
    `line.encode(enc, errors='replace').decode(enc, errors='replace')`
    where `enc = sys.stdout.encoding or 'ascii'`. Also catches other
    `Exception` silently so a broken stdout never aborts the pipeline.
  - This is the same bug class as 2026-04-03's `[PDF\u2713]` -> `[PDF OK]`
    fix; now the defense lives in LogManager, so callers can emit any unicode
    without worrying about Windows GBK.
- **Smoke tests**:
  - `from citationclaw.core.scholar_search_agent import ScholarSearchAgent` OK.
  - `LogManager().info("test \u26a0 \u2713 \u274c")` prints cleanly under
    v2 venv with no UnicodeEncodeError.
- **Harness re-run**: full Phase 2+3 for "Image harmonization" target,
  previously crashed at 663s, now expected to complete cleanly. Phase 3 will
  still fail each V-API call (401) but handle them gracefully (empty scholar
  list per paper) rather than crashing the gather(). This lets
  `merged_authors.jsonl` + `*_results.xlsx` get written -- harness can
  finally measure baseline Health for this target.
- **Status**: COMPLETE (both bugs fixed; harness re-run in progress)
- **Next step**: rotate V-API key to re-enable Phase 3 scholar discovery
  (currently all papers show 0 renowned scholars due to 401).

### 2026-04-18 -- Harness PDF-focus mode surfaced 3 NEW Phase-2 bugs

Harness was refocused to emphasise "did Phase 2 download the RIGHT PDF?".
The new "PDF Correctness" dimension (35% weight) independently re-opens every
cached PDF with PyMuPDF and word-overlaps first-page text against
`Paper_Title`. This catches stale/corrupt cache that the downloader's own
guard missed (cache-hits skip the guard). First run on the "Image
harmonization" target (14 papers) scored A/91.5 overall, but exposed:

1. **Corrupt cache entry: Latin-1 -> UTF-8 mojibake on PDF bytes**
   - File: `data/cache/pdf_cache/f4334037b1ed48e299ad0a486efbf8fc.pdf`
     (5.6 MB, 13 pages, `%PDF-1.7` header looks OK).
   - Stream bytes are doubly-encoded: original binary `\xe2\xe3\xcf\xd3`
     (PDF's mandatory non-ASCII marker) appear as `\xc3\xa2\xc3\xa3\xc3\x8f\xc3\x93`.
   - PyMuPDF: `library error: zlib error: incorrect header check` -> 0 chars
     extracted from page 1 -> harness flags as "mismatched".
   - Because source was marked trusted (title-match skipped at cache write),
     PDFDownloader accepted it without re-verify.
   - **Grep target in `core/pdf_downloader.py`**: `response.text`,
     `.decode("latin-1")`, `.encode("utf-8")` on bytes, any string-manipulation
     on binary payloads before `open(...).write()`.
2. **`PDF_Download=True` but cache file missing**
   - 2/14 papers today: "Deep image harmonization with globally guided..."
     and "BSTNet for Content-Fixed Image Harmonization".
   - Recorded `PDF_Path` hashes `0fd4dfdee...` and `e912761...` are never
     written to `data/cache/pdf_cache/` despite `[PDF OK]` log lines.
   - Likely cause: `PipelineAdapter.to_legacy_record(pdf_path=...)` computes
     the path under a different hash key (DOI-based?) than
     `PDFDownloader._cache_path()` uses (title-based?). Handoff is broken.
   - Impact: Phase 4 citation-description extraction and any dashboard
     consumer of `PDF_Path` will silently skip these papers.
   - **Audit targets**: `app/task_executor.py` around line 500 (where
     `pdf_path` becomes `_pdf_rel` in the record) and `core/pdf_downloader.py`
     `_cache_path` / `download` return value.
3. **DOI-dedup collapses 2 Phase-1 rows to 1 cache file** (not necessarily a
   bug): "Retrieval-augmented image harmonization" and "Retrieval Augmented
   Image Harmonization" both map to `ab5c8f32...`. Expected -- they are the
   same paper -- but it means `merged_authors.jsonl` has 14 rows for
   effectively 13 unique works. If downstream code assumes 1-to-1 mapping
   from Phase-1 rows to PDFs, note the de-dup early.

- **Reproduction**: `eval_toolkit/phase12_harness/run.sh --grep "Image harmonization"`;
  inspect `runs/<ts>/harness_report.md` -> section "Verify".
- **Status**: 3 bugs OPEN; the harness itself needs no further changes to
  find them -- it will catch the same issues on every run until fixed.
- **Suggested first action**: delete the corrupt cache entry
  (`rm data/cache/pdf_cache/f4334037b1ed48e299ad0a486efbf8fc.pdf`)
  to unblock re-downloads, then fix the underlying mojibake.
