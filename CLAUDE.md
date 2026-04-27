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

### 2026-04-03 / 2026-04-04 -- Phase A/B implementation (ARCHIVED)

The original 7 dev-log entries for Phase A (ScraperAPI publisher channel)
and Phase B (V-API search-powered fallback), including the 100-paper
benchmark, were moved to `CLAUDE.ARCHIVE.md` on 2026-04-20 to keep this
file reasonably sized. Short pointer below; see the archive for the full
implementation notes, test tables, and benchmark breakdowns.

- 2026-04-03 -- Initial architecture analysis (12-tier cascade review,
  ScraperAPI under-leveraged, V-API opportunity identified)
- 2026-04-03 -- Phase A: `_detect_publisher`, `_SCRAPER_PUBLISHER_PROFILES`,
  `_scraper_publisher_download`, publisher-specific PDF extractors, cascade
  reorder (Unpaywall -> #1, ScraperAPI publisher -> #11)
- 2026-04-04 -- Live test without ScraperAPI (6/7, 86%)
- 2026-04-04 -- Live test with ScraperAPI (7/8, 88%; Elsevier downgraded
  to premium+us due to standard-plan constraint)
- 2026-04-04 -- Smoke test: found OpenAlex wrong-PDF bug, added
  `_pdf_title_matches` verification guard
- 2026-04-04 -- Phase B: `_llm_search_alternative_pdf` via
  search-grounded gemini-3-flash-preview-search
- 2026-04-04 -- 100-paper benchmark: baseline **75/100** correct,
  improved **96-100/100** correct (+21pp TRUE gain; baseline had 22
  wrong-paper downloads masked as successes)

### 2026-04-13 / 2026-04-14 / 2026-04-18 -- Pre-week dev work (ARCHIVED)

Eight dev-log entries spanning 2026-04-13 through 2026-04-18 were moved
to `CLAUDE.ARCHIVE.md` on 2026-04-21 to keep this file under the
~1500-line threshold (was 1610 lines). Short pointer below; see the
archive for full notes, test tables, and cascade diagrams.

- 2026-04-13 -- End-to-end pipeline testing session:
  `test_papers.py` rewrite (bare-title -> full Phase1+Phase2);
  bug fixes (LLM 429 auto-disable, wrong-paper acceptance, model
  mismatch, GS sidebar silent fail, UI config overwrite);
  new tiers 9b arXiv-title-search, 9c OpenReview-title-search;
  figshare URL transform; persistent `run.log`; CDP proxy-bypass +
  Chrome-first; `disable_llm_search`; 19-tier cascade documented.
- 2026-04-14 -- Fix: OpenReview dead revision IDs
  (`_search_openreview` now returns `List[str]`, loops each candidate
  via ScraperAPI first then direct; verified DiffHarmony++ rescue).
- 2026-04-18 -- Added dev-history-sync Claude Code Skill
  (workspace-root `.claude/skills/dev-history-sync/SKILL.md`).
- 2026-04-18 -- Sibling `eval_toolkit/phase12_harness/` MVP
  (14-target fixtures, contract check, Phase2+3 runner, JSON/MD/HTML
  reports, 6-dim health score).
- 2026-04-18 -- Harness first run surfaced 2 latent production bugs
  (`scholar_search_agent.py` missing `import asyncio`; GBK crash in
  `log_manager.py` print on U+26A0). V-API 401 also re-confirmed.
- 2026-04-18 -- Fix: both bugs above
  (module-level `import asyncio`; `_best_effort_utf8_console()` + GBK
  fallback in `log_manager._log`). Smoke-tested under v2 venv.
- 2026-04-18 -- Harness PDF-focus rescoring surfaced 3 NEW Phase-2
  bugs: (1) Latin-1->UTF-8 mojibake in cached PDFs, (2) `PDF_Download=True`
  but cache file missing (hash-key mismatch between adapter and
  downloader), (3) DOI-dedup cache collision between near-duplicate
  Phase-1 rows. All three fixed on 2026-04-19 (see next entry).

### 2026-04-19 -- UI save silently wiped `core_api_key` from config.json

Secondary issue surfaced while wiring CORE key: `core_api_key` is defined
in `AppConfig` (`config_manager.py`) and is read correctly by
`task_executor.py` -> `PDFDownloader`, BUT it is NOT in the `ConfigUpdate`
pydantic model in `app/main.py` and is NOT edited by the UI. When the user
saves any UI-editable setting, the POST-body round-trip (GET existing ->
merge with form body -> POST) pipes through `ConfigUpdate`. Pydantic v2's
`extra='ignore'` silently drops unknown keys, so `core_api_key` vanishes
and `AppConfig(**data)` writes back `""` into `config.json`.

- **Fix A** (`citationclaw/app/main.py`): add `core_api_key: str = ""` to
  `ConfigUpdate` so the merged POST body preserves the key.
- **Fix B** (`citationclaw/app/main.py`): harden `POST /api/config` with a
  "sensitive-key preservation" loop -- empty-string values in the POST
  body no longer overwrite non-empty stored values for `core_api_key`,
  `s2_api_key`, `mineru_api_token`, `openai_api_key`, `api_access_token`,
  `api_user_id`. This also fixes a latent risk for the other five keys.
- **Tests**: manual check confirms `ConfigUpdate(core_api_key="x")` now
  round-trips; full pytest suite still 165 passed.
- **Not done**: adding an actual UI input for CORE key. The current state
  is safe (config.json value persists across UI saves), but users who
  never touch config.json won't discover the CORE feature. Future work:
  add `<input id="idx-core-api-key">` in `templates/index.html` and the
  matching read/write in `static/js/main.js` (mirror the s2_api_key
  pattern).
- **Status**: silent-wipe bug CLOSED; UI surface remains a TODO.

### 2026-04-19 -- All 3 Phase-2 bugs fixed

- **Bug #1 (mojibake caches)**: new `_pdf_bytes_are_mojibake()` in
  `core/pdf_downloader.py` catches both U+FFFD triplets (hard flavor) and
  `\xc3\xXX` doubling (soft/Latin-1-round-trip flavor) at the byte level.
  Called from `_cache_is_valid` AND `_ok` so corrupt bytes can't land in
  cache nor be accepted from it. `_cache_is_valid` no longer returns True
  on the "zero text extracted" path that let corrupt files slip through.
  Scanner cleaned 9 mojibake PDFs (including the flagged `f4334037...`).
- **Bug #2 (relative cache path)**: `DEFAULT_CACHE_DIR` in
  `core/pdf_downloader.py`, `CACHE_FILE` in `core/metadata_cache.py`, and
  `_DEFAULT_CACHE_FILE` in `core/scholar_search_cache.py` now all anchor
  to `app.config_manager.DATA_DIR` (absolute). `task_executor.py` resolves
  `PDF_Path` with `Path.resolve()` so the field stored in
  `merged_authors.jsonl` is absolute. Migrated 33 non-duplicate PDFs from
  the stale harness cache into the canonical cache; salvaged 12 under
  pre-`_normalize_doi` hashes. Removed `eval_toolkit/phase12_harness/data/
  cache/` entirely.
- **Bug #3 (duplicate cache hash)**: `task_executor.py` pre-computes
  `_cache_path` for every non-self-cite paper, detects collisions,
  dispatches only leaders through the download semaphore, and has
  followers await the leader's future. Logged as `[PDF去重] #N ... 与
  #M ... 映射到同一 cache，共享下载结果` for visibility.
- **Tests**: +8 in `test/test_pdf_downloader.py` (TestAbsoluteCacheDir x3,
  TestMojibakeDetection x5). Full suite 165 passed.
- **Status**: all three CLOSED.
- **Next step**: next harness run should show verify: mismatched=0,
  unreachable=0; papers 5 (MDPI) and 11 (BSTNet) will re-download fresh
  into normalized-DOI hash paths; papers 6 and 10 will share one download
  via the dedup mechanism (watch `[PDF去重]` log line).

### 2026-04-20 -- ScraperAPI fix: standard-plan compat + mojibake guard + #15 `_ok()`

The 2026-04-20 harness re-run validated the three Phase-2 bugs from
2026-04-19 and surfaced 3 new ScraperAPI issues. Fixes:

- **`_SCRAPER_PUBLISHER_PROFILES`**: IEEE and Wiley were still on
  `ultra_premium=true` but the deployed key is on the standard
  100k-credit plan (ScraperAPI returns HTTP 500 on that flag). Downgraded
  both to `premium=true` + `render=true` (IEEE keeps `keep_headers=true`
  for multi-hop cookie persistence). This matches the 2026-04-04
  Elsevier downgrade; the profile docstring now calls out the plan
  constraint explicitly.

- **`_smart_scraper_download` mojibake fix**: `render=true` sends the
  origin through a headless browser which re-encodes PDF binary bytes as
  UTF-8 (two flavors: `\xc3\xXX` soft doubling + `\xef\xbf\xbd` hard
  replacement). The cached `%PDF-` header survived but content streams
  failed zlib. Two-part fix:
  - PDF-first strategy: if the target URL looks like a direct PDF
    (`.pdf` suffix / `/pdf/` segment / `pdfft` / `citation_pdf_url`),
    first hop uses `render=false&premium=true`; only escalate to render
    if we got HTML back.
  - Every `resp.content[:5] == b"%PDF-"` acceptance site also checks
    `not _pdf_bytes_are_mojibake(resp.content)`. On rejection, the
    smart path retries once with `render=false`.

- **`_scraper_publisher_download` mojibake fix**: same byte check added
  at 5 return sites (initial render, transformed-URL fallback, PDF-link
  hop, IEEE inner-iframe, direct download). Prevents mojibake from the
  publisher path as well.

- **Cascade step 15 now uses `_ok()`**: the "ScraperAPI + LLM smart
  fallback" previously wrote to cache with a raw
  `data[:5] == b"%PDF-" and len(data) > 1000` check, bypassing both the
  mojibake guard from 2026-04-19 and the title-match verifier. Now calls
  `_ok(data, "scraper_smart")` like every other step. Label was already
  in `_SOURCE_LABELS` so log format is unchanged.

- **Stale cache cleanup**: removed `data/cache/pdf_cache/
  f4334037b1ed48e299ad0a486efbf8fc.pdf` (5665 KB MDPI mojibake from the
  2026-04-20 harness run).

- **Tests** (`test/test_pdf_downloader.py`):
  - Renamed / updated `TestPublisherProfiles`: `test_ieee_uses_premium`,
    added `test_wiley_uses_premium`, tightened `test_elsevier_uses_premium`.
    All three assert `"ultra_premium" not in profile`.
  - Updated `TestScraperBuildUrl.test_build_ieee_url` to assert
    `premium=true` and explicitly forbid `ultra_premium=true`.
  - New class `TestScraperApiMojibakeIntegration` (3 tests):
    - `test_scraper_smart_label_registered`
    - `test_smart_scraper_url_picks_raw_fetch_for_pdf_urls` (source
      inspection: `pdf_like` branch present; `_pdf_bytes_are_mojibake`
      called)
    - `test_no_publisher_profile_uses_ultra_premium` (future-proof
      global invariant)
  - Full suite: 190 passed, 1 failed, 1 error -- both failures are
    the 2026-04-19 pre-existing ones (no regressions).

- **Status**: fixes SHIPPED, not yet validated against live harness.
- **Next step**: re-run `phase12_harness --grep "Image harmonization"`
  to confirm Paper 5 downloads a clean PDF (no verify mismatch), and
  BSTNet IEEE either succeeds or at least stops returning 500.

### 2026-04-20 -- V-API activation + candidate-URL ScraperAPI rescue

The 2026-04-20 harness log proved `[PDF下载] CDP: ... LLM搜索: 禁用`;
all 4 failed papers missed cascade step 13 (`_llm_search_alternative_pdf`).
Fix: flip the opt-in config flag + make V-API's candidate URLs benefit
from the ScraperAPI fix landed in the previous entry.

- **`config.json`**: `enable_pdf_llm_search: false` -> **`true`**.
  Default in `config_manager.py` is unchanged (stays opt-in for new
  users without a working V-API key). This deployment already has a
  valid `gpt.ge` key + `gemini-3-flash-preview-search` model.

- **`core/pdf_downloader.py`**:
  - **New helper `_scraper_fetch_url(url)`**: minimal ScraperAPI proxy
    fetch for a known target URL. Inherits the render-gating policy of
    `_smart_scraper_download` (`.pdf` / `/pdf/` / `pdfft` / `citation_pdf_url`
    -> `render=false`, everything else -> `render=true`, mojibake ->
    single `render=false` retry). No link extraction / LLM.
  - **`_llm_search_alternative_pdf` candidate loop**: for each LLM
    candidate URL, direct fetch first (unchanged), then on failure
    route through `_scraper_fetch_url`. Rescues V-API's ResearchGate /
    institutional-repo URLs that block datacenter IPs. Mojibake guard
    and title-match check applied on both paths.

- **Tests** (`test/test_pdf_downloader.py`, new `TestVApiIntegration`
  class, 4 tests):
  - `test_config_json_has_llm_search_enabled` — asserts the deployed
    config stays on.
  - `test_scraper_fetch_url_helper_exists`
  - `test_scraper_fetch_url_no_keys_returns_none`
  - `test_llm_search_calls_scraper_rescue` (source inspection).
  - Full suite: 194 passed, 1 failed, 1 error (pre-existing).

- **Cascade order unchanged**. Step 13 (V-API search, now active) runs
  before step 14 (curl) and step 15 (ScraperAPI smart). Papers where
  ScraperAPI publisher (step 11) fails now get a real V-API attempt
  before falling through to paid smart-scraper credits.

- **Status**: COMPLETE. Expected effect on next harness: 2-3 of the 4
  failed papers from the 2026-04-20 run should recover via arXiv /
  preprint alternatives (matches 2026-04-04 Phase B results).

### 2026-04-20 -- V-API live-probe: upstream 429 retry + kill SDK auto-retry

Ran a live probe against `gpt.ge` with `_llm_search_alternative_pdf`.
Two new failure modes observed that the previous code handled poorly:

1. **First-attempt upstream 429** -- the search-grounded Gemini model
   answers 429 `upstream_error` ("负载已饱和") on almost every cold
   query, but a 5-15s backoff converts that into a success. Old
   behaviour: single 429 disabled LLM search for the entire run.
2. **OpenAI SDK auto-retry compounding** -- default `max_retries=2`
   against a 90s httpx timeout burned up to 270s per failed paper,
   stalling the harness.

Fix (`core/pdf_downloader.py`):

- 3-attempt inner retry around `client.chat.completions.create` with
  0 / 5 / 15s backoff; only fires on 429 / `upstream_error` / 负载;
  other errors (401 / 403 / timeout) fail fast.
- Outer handler split into **auth** (disable immediately), **429
  circuit breaker** (`_llm_search_429_misses`, disable only after 3
  misses across the run), and **other** (log and continue).
- `AsyncOpenAI(..., max_retries=0)` on both call sites — SDK retries
  were the hidden cause of the 195s+ stalls seen in the first live
  test.
- httpx timeout held at 90s (60s killed legitimate slow searches).

Live probe (4 real titles, cold cache):

| Paper | OK? | Time | Notes |
|---|---|---|---|
| Attention Is All You Need | 2163 KB | 59.9s | 2× 429 retry, arXiv |
| DiffHarmony++ (ACM) | NO | 131s | 3× 429, timed out on attempt 3 |
| BSTNet (IEEE) | NO | 48.9s | LLM: "未找到替代PDF源" (honest) |
| MAE | 7271 KB | 65.8s | 2× 429 retry, arXiv |

`disabled=False` and `429_misses=0` throughout — the retry layer
fully absorbed the upstream saturation. 2/4 success for well-known
papers is expected behaviour when (a) only arXiv/repo-hosted versions
exist and (b) upstream saturation is real.

Tests (`test/test_pdf_downloader.py`):

- `TestVApiIntegration.test_llm_search_own_retry_loop_and_no_sdk_retry`
  — source-inspection lock for `max_retries=0`, the 429 retry loop,
  and the circuit-breaker counter.
- Full suite: 72 passed (71 → 72).

Status: V-API on gpt.ge is **VALIDATED + STABLE** for harness runs;
upstream 429 storms no longer disable the run.

### 2026-04-20 -- UI save silently wiped `enable_pdf_llm_search` (same pattern as 2026-04-19 `core_api_key`)

User ran the pipeline end-to-end via the FastAPI UI and reported "效果
非常差" despite having asked for LLM search to be enabled. The run.log
startup line was definitive: `[PDF下载] CDP: 未启用, LLM搜索: 禁用`.
Cross-checking `config.json` showed both `enable_pdf_llm_search` and
`cdp_debug_port` had been reset (to `false` and `0`) AFTER we flipped
them on earlier in the day. `core_api_key` was preserved as the user
expected.

Root cause: the UI's `POST /api/config` body schema `ConfigUpdate` in
`app/main.py` did NOT contain `enable_pdf_llm_search`. Any UI save
(even unrelated, e.g. adding `core_api_key`) went through:
  1. UI GET current config (has `enable_pdf_llm_search: true`).
  2. UI user edits unrelated field, POST the merged body.
  3. Pydantic validates into `ConfigUpdate` — silently drops the
     missing field.
  4. `AppConfig(**data)` rebuilds with the DEFAULT value `False`.
  5. `config_manager.save(new_config)` writes `false` to disk and
     overwrites the in-memory cache.
  6. Next task run shows `LLM搜索: 禁用`.

Identical failure mode to the 2026-04-19 `core_api_key` bug (which that
day's fix added to the schema + sensitive-preservation list). This
round patches the same class of bug globally.

Secondary cause: `ConfigManager` cached the config at startup and never
re-read disk — so a manual re-flip of `enable_pdf_llm_search: true` in
`config.json` had no effect until the FastAPI server was restarted.

- **`app/main.py`**:
  - Added `enable_pdf_llm_search: bool = False` to `ConfigUpdate`
    schema.
  - Added `enable_pdf_llm_search` to the sensitive-key preservation
    list in `POST /api/config`. The existing check
    `if not data.get(key) and existing.get(key)` evaluates to
    preservation when the POST body carries `False` but disk has
    `True` — so a UI widget that doesn't surface this flag cannot
    accidentally flip it off.

- **`app/config_manager.py`**:
  - `ConfigManager` now tracks `_disk_mtime` and `get()` auto-reloads
    when `config.json` has been modified since the last load. Manual
    edits to `config.json` take effect immediately; no server restart
    needed. `save()` keeps the tracker in sync so it doesn't loop-read
    our own writes.

- **`config.json`**: re-flipped both values that had been wiped:
  - `enable_pdf_llm_search: false -> true`
  - `cdp_debug_port: 0 -> 9222` (the user has Chrome debugging
    running on that port; it was also being reset because the UI
    form value was 0 even though the schema preserved it).

- **Tests** (`test/test_pdf_downloader.py`, added to
  `TestVApiIntegration`):
  - `test_config_update_schema_has_llm_search_field` -- symbolic
    lock on the missing-field fix.
  - `test_config_update_schema_preserves_all_app_fields` -- future-
    proof: diffs `AppConfig` vs `ConfigUpdate` and fails on any
    field that would be silently wiped on round-trip (exempts
    `enable_year_traverse` which is explicitly reset every startup).
  - `test_config_manager_auto_reloads_on_disk_change` -- writes a
    config.json, reads it, rewrites with a flipped value, verifies
    `get()` returns the new value without re-instantiation.
  - Full suite: 75 passed (72 -> 75).

- **Status**: CLOSED. Next pipeline run (server restart required for
  the `ConfigManager` auto-reload code to be loaded) should show
  `CDP: 端口 9222 已连通, LLM搜索: 启用`.

### 2026-04-20 -- Phase 2 login checkpoint (auto-pop publisher login pages)

User ask: "能不能更加自动化一点，运行到phase2之前自动弹出登录页面".
Previously the `_cdp_ensure_browser` helper could auto-launch Chrome/Edge
with `--remote-debugging-port` + a persistent `runtime/debug_browser_profile/`
user-data-dir, but it only fired on-demand from within a PDF download
attempt (too late: user has to notice the window, switch over, log in
while papers are already racing through the cascade). Wired up a proper
pre-Phase-2 checkpoint that opens login tabs the moment metadata+download
begins and blocks until the user clicks 继续 or a configurable timeout.

- **New config fields** (`citationclaw/app/config_manager.py` +
  `citationclaw/app/main.py` `ConfigUpdate`):
  - `enable_phase2_login_checkpoint: bool = True` — gated by
    `cdp_debug_port > 0` so users without CDP see zero behavior
    change; added to the 2026-04-19/2026-04-20 sensitive-key
    preservation list in `POST /api/config` so a UI round-trip
    without the widget can't silently flip it off.
  - `phase2_login_urls: list[str]` — defaults to IEEE / Springer /
    Elsevier / ACM / Wiley landing pages (each triggers the
    institutional SSO prompt when the user hits 登录).
  - `phase2_login_wait_seconds: int = 180` — max block time.

- **New `_cdp_open_login_pages(debug_port, urls)` helper**
  (`citationclaw/core/pdf_downloader.py`): reuses `_cdp_open_page`
  in a loop with per-URL try/except so one bad URL doesn't kill the
  whole checkpoint. Returns count of tabs opened. Returns 0 when
  CDP is not reachable, no exception.

- **`TaskExecutor._prompt_phase2_login(config)`** (new, in
  `citationclaw/app/task_executor.py`):
  1. Early-return if `cdp_debug_port<=0`, flag off, or already
     completed this task run (one-shot via `_phase2_login_done`).
  2. Calls `_cdp_ensure_browser(port)` (idempotent if already alive).
  3. Calls `_cdp_open_login_pages(port, urls)` to pop the tabs.
  4. Broadcasts `phase2_login_prompt` WebSocket event with
     `{urls, wait_seconds, cdp_port}` payload.
  5. `await asyncio.wait_for(self._phase2_login_event.wait(),
     timeout=wait_seconds)` — same pattern as
     `_year_traverse_event` (2025-04-04 precedent, proven design).
  6. Timeout path logs a warning and continues with existing cookies
     — users who were already signed in from a prior run never
     actually have to interact with the modal.
  - Called at the top of `_run_new_phase2_and_3` (runs BEFORE
    target-author metadata query, so logging in can overlap with
    the slowest part of the pipeline if the user is fast).

- **New REST endpoint** `POST /api/task/phase2-login-ready`
  (`citationclaw/app/main.py`): sets
  `task_executor._phase2_login_event`. Returns 400 if no event is
  armed (verified via `TestClient`). Same shape as
  `/api/task/year-traverse-respond`.

- **New UI surface**:
  - `citationclaw/templates/index.html`: new `phase2LoginModal`
    (Bootstrap static-backdrop modal, mirrors `yearTraverseModal`).
    Shows the opened URLs as a clickable list, a live countdown
    timer, and two buttons (`已登录，继续 Phase 2` /
    `跳过，直接继续`).
  - `citationclaw/static/js/main.js`: `ws.on('phase2_login_prompt',
    ...)` handler renders URL list + starts client-side countdown
    (display only, real timeout enforced server-side), both buttons
    POST `/api/task/phase2-login-ready` and hide the modal.

- **Smoke tests** (manual):
  - `pytest test/test_pdf_downloader.py` — 75 passed (unchanged).
  - `AppConfig()` instantiates with the 3 new fields at their
    documented defaults.
  - `ConfigUpdate.model_fields` contains all 3 new fields; default
    URL list matches.
  - `_cdp_open_login_pages(65500, [...])` returns 0 (no-connection
    no-op path).
  - `TaskExecutor` instance exposes `_phase2_login_event`,
    `_phase2_login_done`, and `_prompt_phase2_login`; method source
    references `_cdp_ensure_browser`, `_cdp_open_login_pages`, and
    broadcasts `phase2_login_prompt`.
  - `TestClient.post('/api/task/phase2-login-ready')` with no event
    armed returns 400 with the expected message.

- **Behavior preservation guarantees**:
  - `cdp_debug_port == 0` (default shipping config) → checkpoint
    is a pure no-op, pipeline behaves byte-identically to before.
  - Checkpoint runs once per task (guarded by `_phase2_login_done`),
    so multi-paper harness runs don't pop the modal N times.
  - `websocket-client` missing → logs a warning and skips gracefully
    (checkpoint can't drive CDP without it).
  - Timeout path never raises — pipeline always progresses.

- **Status**: COMPLETE. **Server restart required** for the new
  route + WebSocket event handler to be loaded. Next full pipeline
  run with `cdp_debug_port=9222` should show
  `[Phase2登录] 已弹出 5 个出版商页面…` plus the UI modal.

- **Next step**: add an "I'm already logged in, don't prompt again
  for N hours" option backed by a `runtime/phase2_login_stamp.json`
  sentinel, so returning users can entirely skip the modal after
  the first run of the day.

### 2026-04-20 -- Phase 2 login checkpoint smoke test + unearthed `import json` regression

Kicked off a smoke test of the checkpoint via
`phase12_harness --grep "Image harmonization"` (14 papers).

**First run exposed a pre-existing silent bug**: the initial harness
log showed `[Phase2登录] 无法启动调试浏览器（port=9222）` even though
`netstat` confirmed port 9222 was actively LISTENING (Chrome PID 2116
launched earlier, still alive). Reproduced the symptom by importing
`_cdp_check_connection` directly and calling it: returned False. But
inlining the exact same function body in a standalone script and
running against the same port: returned True.

Root cause (found by printing the function's `__globals__`):
`pdf_downloader.py` **was missing a top-level `import json`**.
Every CDP helper (`_cdp_check_connection`, `_cdp_list_tabs`,
`_cdp_open_page`, `_cdp_call`, `_cdp_fetch_pdf_in_context`, etc.)
calls `json.loads(...)` / `json.dumps(...)`. Without the import,
each call raised `NameError: name 'json' is not defined`, got
swallowed by the blanket `except Exception: return False / return {}`,
and the CDP tier quietly reported "never connected" for every
caller. Been shipping this way since some unknown refactor. The
only reason CDP ever appeared to work was when a **foreign** Chrome
was already listening on 9222 from an earlier manual launch — which
doesn't exercise our `_cdp_ensure_browser` spawn path.

**Fix** (1 line, `core/pdf_downloader.py`):
- Added `import json` at module top with a docstring comment
  explicitly calling out the silent-failure mode as a don't-remove
  guardrail for future refactors.

**Regression tests** (`test/test_pdf_downloader.py`, new
`TestCdpHelpers` class, 3 tests):
- `test_pdf_downloader_imports_json_at_module_level` — asserts
  `hasattr(pdl, "json") and callable(pdl.json.loads)`.
- `test_cdp_check_connection_function_has_json_in_globals` —
  belt-and-suspenders check on `__globals__`.
- `test_cdp_open_login_pages_returns_int` — smoke test that the
  new Phase 2 login helper returns 0 on no-connection and empty
  URL list without raising.
- Full suite: **78 passed** (was 75), 1 pre-existing failure
  (`test_citing_description_cache` async loop, unchanged).

**Second harness run** (with the fix, same target, clean start):
- `21:34:09 [INFO] [Phase2登录] 已弹出 5 个出版商页面` — checkpoint
  fired correctly, 5 publisher tabs opened via CDP.
- `21:34:29 [WARNING] 等待超时（20s），按现有 cookies 继续` —
  graceful timeout (harness has no UI to click 继续, so we
  configured `phase2_login_wait_seconds=20` just for this run).
- `21:34:52 [INFO] [PDF下载] CDP: 端口 9222 已连通, LLM搜索: 启用`
  — flipped from "未连通" (before fix) to "已连通" (after fix).
- `21:39:12 [INFO] [PDF OK] CDP-IEEE (1303KB): Image Harmonization
  Algorithm based on M` — **first observed CDP-IEEE success in
  this repo**. An IEEE paper (TPAMI arnumber 11193236) downloaded
  via the authenticated Chrome session using cookies from PID 2116.
  Took ~30s including the cascade walk (`ScraperAPI render → 500,
  ScraperAPI direct → ResearchGate → Sci-Hub direct → Sci-Hub via
  ScraperAPI → CDP-IEEE ✓`).
- At 5 min mark: 9/14 OK (8 cached + 1 CDP-IEEE new), 4 papers
  still retrying through ScraperAPI + Sci-Hub. Background task
  continues.

**Also surfaced (not fixed this session, flagged for follow-up)**:
1. `config.json`'s `openai_api_key` has a leading-space typo
   (`" sk-o37..."`). LLM search tripped this at `21:36:36`:
   `[LLM搜索] 认证/计费失败 Error 401 无效的令牌`. Circuit
   breaker then disabled LLM search for the rest of the run
   (behavior from 2026-04-20 V-API retry fix working as intended).
   Fix: strip leading whitespace from the key in config.json.
2. `_cdp_ensure_browser` uses `Path("runtime/debug_browser_profile")`
   — a RELATIVE path. Harness running from `eval_toolkit/
   phase12_harness/` creates its own profile dir at
   `eval_toolkit/phase12_harness/runtime/debug_browser_profile/`
   instead of the canonical v2 project root. Same pattern as the
   `DEFAULT_CACHE_DIR` relative-path bug fixed on 2026-04-19.
   Impact: login cookies saved via FastAPI UI (writes to v2
   `runtime/`) don't get picked up by harness runs, and vice versa.
   Follow-up: anchor the profile dir to `DATA_DIR.parent` or to
   `Path(__file__).resolve()` parentage, mirroring the 2026-04-19
   fix.

**Status**: login checkpoint + json-import fix CLOSED. 3 follow-ups
OPEN (V-API key whitespace, profile-dir relative path, UI-triggered
modal needs server restart to pick up new route).

### 2026-04-20 -- Fixed two follow-ups flagged by smoke test (whitespace-key + relative profile-dir)

Both bugs from today's smoke-test dev log turned out to be classic
quiet-landmine patterns — impossible to feel until you stub your toe
on them, then retroactively "obvious". Fixed together.

**Fix 1: `openai_api_key` leading-space auto-strip**

Live `config.json` had `"openai_api_key": " sk-o37..."` (copy-paste
artifact from the V-API console). OpenAI's auth header is strict
about whitespace → every call 401'd → `_llm_search_alternative_pdf`
circuit breaker triggered at the first request and disabled LLM
search for the entire run. User had zero clue because the only
signal was one line reading `[LLM搜索] 认证/计费失败 ... 无效的令牌`
buried in a 500-line log.

- `citationclaw/app/config_manager.py`:
  - New module-level tuple `_SENSITIVE_STRIP_FIELDS` enumerating
    every `AppConfig` field that carries a secret or a URL/model
    name (`openai_api_key`, `openai_base_url`, `openai_model`,
    `s2_api_key`, `core_api_key`, `mineru_api_token`,
    `api_access_token`, `api_user_id`,
    `renowned_scholar_model`, `author_verify_model`,
    `dashboard_model`).
  - Pydantic v2 `@field_validator(*_SENSITIVE_STRIP_FIELDS,
    mode="before")` strips whitespace BEFORE type coercion, so
    whatever sneaks in from disk / UI POST / env injection gets
    trimmed. Handles all 3 entry paths (JSON load, UI save,
    direct AppConfig() construction in tests) uniformly.
- `config.json`: removed the offending leading space directly so
  the currently-running server (once restarted) immediately sees
  the correct key.

**Fix 2: CDP debug-browser profile dir → absolute path**

`_cdp_ensure_browser` used `Path("runtime/debug_browser_profile")`,
which resolves against CWD. Three consequences observed today:
- Harness (CWD=eval_toolkit/phase12_harness/) creates its own
  profile in that sibling runtime/, so logins saved via the
  FastAPI UI (CWD=v2 root) are invisible to harness runs and
  vice versa.
- The 20 KB "fresh" profile under v2/runtime/ is a throwaway from
  my 21:28 debug script; the 65 KB profile with real IEEE cookies
  was in the harness subdir, lucky survivor of past manual
  launches (PID 2116).
- Same bug class as the 2026-04-19 `DEFAULT_CACHE_DIR` relative-
  path fix.

- `citationclaw/core/pdf_downloader.py`:
  - New module-level constant `DEBUG_BROWSER_PROFILE_DIR =
    _DATA_DIR.parent / "runtime" / "debug_browser_profile"`
    (alongside `DEFAULT_CACHE_DIR`), with the 2026-04-19-style
    try/except fallback on `Path(__file__).resolve()` parentage.
  - `_cdp_ensure_browser` body: replaced local
    `profile_dir = Path("runtime/debug_browser_profile")` /
    `{profile_dir.resolve()}` with the module constant.
  - Did NOT migrate the harness-profile's cookies across: copying
    a live SQLite Cookies file while PID 2116 is still writing
    WAL entries risks corruption, and the one-time "log in once
    in the canonical profile" cost is trivial. User just logs in
    once next run and it sticks forever.

**Regression tests** (5 new, `test/test_pdf_downloader.py`):
- `TestCdpHelpers.test_debug_browser_profile_dir_is_absolute`
  asserts the constant is an absolute Path under a `/runtime/
  debug_browser_profile` suffix.
- `TestCdpHelpers.test_cdp_ensure_browser_uses_absolute_profile`
  source-inspection guard: function body references the constant
  and does not contain the old `Path("runtime/...")` literal.
- `TestVApiIntegration.test_app_config_strips_leading_space_in_api_key`
  mirrors the exact historical bug: `" sk-abc123\n"` → `"sk-abc123"`.
- `TestVApiIntegration.test_app_config_strips_all_sensitive_fields`
  iterates every `_SENSITIVE_STRIP_FIELDS` entry for future-proofing.
- `TestVApiIntegration.test_config_manager_strips_disk_json_whitespace`
  end-to-end: write a JSON file with leading-space values, assert
  `ConfigManager.get()` returns trimmed values.
- Full suite: **83 passed** (was 78). Pre-existing
  `test_citing_description_cache` async-loop failure unchanged.

**Background harness observation** (from the still-running 21:34
run, pre-existing as I was coding these fixes):
- 10/14 OK at the time of writing. **Two** CDP-IEEE successes,
  not one: `[PDF OK] CDP-IEEE (1303KB)` at 21:39 and
  `[PDF OK] CDP-IEEE (1645KB): BSTNet for Content-Fixed Image
  Harmoniza` at 21:42. BSTNet had failed across every prior
  harness run documented in earlier entries — it's the poster
  child for "publisher-IP paywalled paper where only a live
  logged-in session works". The json-import fix is literally
  worth ~2 papers per run on this target.

**Status**: (a) CLOSED. No server restart needed just for Fix 1
(ConfigManager already hot-reloads mtime-changed disk files,
2026-04-20 fix), but the new route / JS for the Phase 2 modal
still needs a restart. Fix 2 takes effect on the next
`_cdp_ensure_browser` invocation — fresh Chrome launches land in
the canonical path.

**Next step**: the "I'm already logged in, don't prompt again for
N hours" sentinel is still OPEN (would tag
`runtime/debug_browser_profile/phase2_login_stamp.json`).

### 2026-04-20 -- CDP per-publisher auth probe (standalone CLI + Phase 2 auto-integration)

User ask: "增加一个测试阶段，在每个网站上都测试下载一个论文，看看
cdp是否成功". Implemented in two layers -- a reusable core module with
a five-state diagnostic machine, plus an inline post-login call from
`TaskExecutor._prompt_phase2_login` so users see per-publisher auth
status BEFORE the ~20 min PDF download phase kicks off. Huge time-
saver when, e.g., ACM still wants step-up auth that the login
checkpoint didn't clear.

- **New `citationclaw/core/cdp_login_probe.py`** (reusable module):
  - `PUBLISHER_PROBES` dict: 5 hand-picked test papers (IEEE ResNet,
    ACM node2vec, Elsevier Schmidhuber survey, Springer ImageNet,
    Wiley autonomous-driving survey). Each entry has
    `{doi, title, landing_url, pdf_url}`. Live-probed 2026-04-20 to
    verify every `landing_url` loads the expected content when
    authenticated.
  - State machine (6 outcomes): `PDF_OK` (landing loads + PDF bytes
    fetched); `AUTH_OK` (landing loads right paper but probe's
    simple PDF URL failed -- auth still green; publishers like
    Elsevier need md5+pid from React-state, probe can't replicate);
    `LOGIN_WALL` (redirect to /login / doc.title says "Sign In");
    `FIXTURE_BROKEN` (landing is a 404 -- our DOI fixture is stale,
    NOT user's problem); `MOJIBAKE`; `ERROR`.
  - `probe_all(port, publishers=None, wait_s=8.0, verbose_log=None)`
    runs probes sequentially, returns `list[ProbeResult]`. Never
    raises -- exceptions become `STATUS_ERROR` results. 8s wait
    matches `_try_cdp_ieee`'s stamp-page settle time.
  - `format_summary(results)` one-liner for log rollups.
  - Status codes exported as constants (`STATUS_PDF_OK`, ...) plus a
    `PASSING_STATUSES` frozenset so callers can ask
    `r.status in PASSING_STATUSES` without string-typing.

- **CLI refactor** (`eval_toolkit/cdp_login_probe.py`):
  - Was a 355-line standalone. Now ~85-line thin wrapper that:
    argparse -> `_cdp_check_connection` preflight -> per-publisher
    `probe_all([one])` loop -> `_print_table` + `format_summary`.
  - All real logic moved to `core.cdp_login_probe`; CLI and pipeline
    integration share it.
  - Backwards-compatible flags: `--port`, `--only`, `--verbose`,
    plus new `--wait <seconds>` (default 8).

- **Task-executor integration** (`citationclaw/app/task_executor.py`):
  - New `TaskExecutor._run_phase2_login_probe(cdp_port)`: imports
    `probe_all`, runs it via `asyncio.to_thread` (probe is blocking
    `time.sleep` + CDP I/O, would stall the asyncio loop), logs
    per-publisher lines (`info` if passing, `warning` if not), then
    a roll-up summary line.
  - Hook point: end of `_prompt_phase2_login`, after the
    `asyncio.Event.wait() / timeout` block, guarded by
    `if getattr(config, "enable_phase2_login_probe", True)`.
  - Non-fatal by design: probe exceptions are caught, logged as
    warnings, pipeline continues.

- **Config flag** (`config_manager.py`, `main.py`, `config.json`):
  - `AppConfig.enable_phase2_login_probe: bool = Field(default=True)`.
  - `ConfigUpdate` mirror + added to the 2026-04-19/2026-04-20
    sensitive-key preservation list in `POST /api/config` (same
    silent-wipe-protection pattern as `enable_pdf_llm_search`,
    `enable_phase2_login_checkpoint`).
  - `config.json` explicitly set to `true` (also the default, but
    writing it makes the setting discoverable in the JSON).

- **Tests** (`test/test_pdf_downloader.py`, new `TestCdpLoginProbe`
  class, 6 tests):
  - `test_probe_module_exposes_public_api` -- dict shape + status
    constants + `PASSING_STATUSES` superset check.
  - `test_probe_all_rejects_unknown_publisher` -- `ValueError` on
    bogus key.
  - `test_probe_all_returns_error_results_on_dead_port` -- dead
    port 65501 yields `STATUS_ERROR`, never raises.
  - `test_format_summary_handles_mixed_results` -- deterministic
    `ProbeResult` -> rollup string.
  - `test_task_executor_calls_probe_when_flag_on` -- source-
    inspection guard that `_prompt_phase2_login` references
    `_run_phase2_login_probe` AND the config flag.
  - `test_app_config_has_enable_phase2_login_probe` -- default
    True + `ConfigUpdate` plumbing.
  - Full suite: **89 passed** (was 83). Pre-existing
    `test_citing_description_cache` failure unchanged.

- **Live verification**: ran the refactored CLI wrapper
  (`--only ieee,acm`) against the same port 9222 that the
  122-paper harness was actively using. Both returned `PDF_OK`
  (289 KB + 1281 KB) in ~9s each. Concurrent harness + probe
  operation is fine -- Chrome's tab lifecycle isolates the probe's
  landing tab from harness's in-flight CDP-IEEE work.

- **Expected UX** (next pipeline run, post server-restart):
  ```
  [Phase2登录] 请在弹出的浏览器中完成出版商登录... 180s 后自动继续。
  [Phase2登录] 用户已确认登录完成，继续 Phase 2
  [Phase2验证] 正在验证 5 个出版商的 CDP 认证状态 (~50s)...
    [ieee     ] [PDF OK]    ( 9.6s)  289 KB title-match: yes
    [acm      ] [PDF OK]    ( 8.5s)  1281 KB title-match: yes
    [elsevier ] [AUTH OK]   ( 8.3s)  landing loaded (...) -- PDF direct fetch ...
    [springer ] [AUTH OK]   ( 8.6s)  landing loaded (...) -- PDF direct fetch ...
    [wiley    ] [AUTH OK]   ( 8.6s)  landing loaded (...) -- PDF direct fetch ...
  [Phase2验证] 5/5 出版商认证通过 (AUTH_OK:3, PDF_OK:2)
  ```
  If any publisher shows `LOGIN_WALL` the user knows immediately
  (not 15 min into the run) to go back and finish that site's login.

- **Status**: COMPLETE. Server restart required for
  `_run_phase2_login_probe` to be loaded in the already-running
  FastAPI instance.

- **Next step**: the 24h-no-prompt-again sentinel is still the
  lowest-hanging remaining UX improvement.

### 2026-04-20 -- Live harness validation of Phase 2 auto-probe (target: Multi-mode interactive, 13 papers)

Ran `phase12_harness --grep "Multi-mode interactive"` with
`phase2_login_wait_seconds=10` (reduced just for this smoke run so
the already-logged-in Chrome doesn't sit idle for 3 min) to verify
the full (B)-integration chain end-to-end. Final harness report:

| Metric | Value |
|--------|-------|
| composite health | **94.2 / A** |
| elapsed | 1028s (~17 min) |
| download | 10/12 (83.3%) |
| verify | **10/10 (100%)** — zero mismatched, zero unreachable |
| renowned scholars | 6 (incl. Ming-Hsuan Yang 4×Fellow, Anis Yazidi 挪威技术院士) |

**Critical timestamps in `run.log` proving (B) works**:
```
[22:49:41] [Phase2登录] 已弹出 5 个出版商页面
[22:49:41] [Phase2登录] 请在弹出的浏览器中完成出版商登录... 10s 后自动继续
[22:49:51] [Phase2登录] 等待超时（10s），按现有 cookies 继续
[22:49:51] [Phase2验证] 正在验证 5 个出版商的 CDP 认证状态 (~50s)...
[22:50:36]   [ieee     ] [PDF OK]    ( 9.9s)  289 KB title-match: yes
[22:50:36]   [acm      ] [PDF OK]    ( 8.5s)  1281 KB title-match: yes
[22:50:36]   [elsevier ] [AUTH OK]   ( 8.3s)  landing loaded (title='请稍候…')
[22:50:36]   [springer ] [AUTH OK]   ( 9.1s)  landing loaded (title='ImageNet Large Scale...')
[22:50:36]   [wiley    ] [AUTH OK]   ( 8.6s)  landing loaded (title='A survey of deep learning...')
[22:50:36] [Phase2验证] 5/5 出版商认证通过 (AUTH_OK:3, PDF_OK:2)
[22:50:36] [自引检测] 查询目标论文作者: ...
```

Timing: 45s from checkpoint-timeout to probe-summary, then
immediately into Phase 2 metadata. The probe adds ~45s overhead to
runs where login checkpoint fires; on runs where `cdp_debug_port=0`
or `enable_phase2_login_probe=false` the whole block is a no-op.

**Interesting finding**: Elsevier's probe landing returned
`title='请稍候…'` (= Cloudflare's "just a moment..." challenge page,
not Elsevier content). Auth was still reported as AUTH_OK because
no login redirect. Seconds later in the real download path the
harness actually tried the same ScienceDirect endpoint and logged
`[CDP-Elsevier] Cloudflare 验证 — 请在浏览器中完成验证` for 120s
before timing out on DiffClick. So the probe's AUTH_OK signal is
necessary but not sufficient -- Cloudflare is a second gate not
captured by landing-URL inspection. Filing this as a known probe
limitation: might upgrade later to catch Cloudflare by looking for
"Just a moment" / "请稍候" / turnstile widget markers in the page
content.

**Two failures** (`DiffClick`, `Doktors der Ingenieurwissenschaften`):
- DiffClick hit Cloudflare+V-API connection error loop. Structural,
  not a regression of (B).
- Doktors is a German PhD thesis with zero public PDF anywhere --
  expected failure.

**V-API connection errors observed throughout this run** (17+ retry
messages). All `[LLM搜索] 异常: Connection error.` The V-API key
401-whitespace fix works (no 401s) but gpt.ge endpoint itself is
flaky right now. Not a regression; just documented for future
investigation.

**Cleanup**: `config.json`'s `phase2_login_wait_seconds` reverted
from 10 to 180 (default).

**Status**: (B) validation CLOSED. Integration is production-ready.

### 2026-04-20 -- CLAUDE.md archived: moved 2026-04-03/04 dev log to CLAUDE.ARCHIVE.md

User flagged `CLAUDE.md` as "now too large" and asked to move entries
older than a week somewhere else. Today is 2026-04-20, so the cutoff
was 2026-04-13 -- anything before that gets archived.

- Ran a one-shot Python script that, for each CLAUDE.md, finds the
  first `### 2026-04-03` header and the first later-date header,
  extracts everything in between, and writes it to a sibling
  `CLAUDE.ARCHIVE.md`. The removed block is replaced with an
  8-bullet pointer summary so anyone reading the live file still sees
  the Phase A/B milestones in chronological order.
- **Line counts before/after**:
  | File | Before | After | Delta |
  |---|---|---|---|
  | `CitationClaw-v2/CLAUDE.md` | 1451 | 1273 | -178 |
  | `CitationClaw-v2/CLAUDE.ARCHIVE.md` | (new) | 213 | |
  | `CLAUDE.md` (top-level) | 1014 | 838 | -176 |
  | `CLAUDE.ARCHIVE.md` (top-level) | (new) | 211 | |
- **Archived entries** (7, all 2026-04-03 / 2026-04-04):
  Initial Analysis; Phase A Implementation; Live Test No-ScraperAPI
  (6/7); Live Test With-ScraperAPI (7/8); Smoke Test wrong-PDF bug;
  Phase B Implementation; 100-paper Benchmark.
- **Updated `.claude/skills/dev-history-sync/SKILL.md`**: new "Where
  to write" note documenting the archive siblings as read-only, and
  the policy that new entries ALWAYS go to the live `CLAUDE.md`, never
  to `CLAUDE.ARCHIVE.md`. If the live file grows back over ~1500
  lines, manual re-archive is the signal (not automatic).
- **Updated `Conventions` section** in both CLAUDE.md files with a
  one-liner pointing at the archive.

**Status**: CLOSED. Live files now both under 1300 lines; archives
preserve every word of the old entries for anyone needing the
implementation-era detail (ScraperAPI publisher profile deep dive,
100-paper benchmark breakdown, etc.).

### 2026-04-21 -- scripts/annotate_paper_results.py: xlsx post-run annotator

User asked for the per-paper multi-run comparison spreadsheet
(`D:/PROJECT/citationclaw/paper_results.xlsx`, sheet "后") to be
updated with:
  - a new PDF_Download column reflecting the latest run's results
  - color coding (green=success, red=fail, gray=unknown)
  - an inserted annotation row below every FRESHLY DOWNLOADED paper
    showing which tier pulled the bytes this run

Delivered as `D:/PROJECT/citationclaw/scripts/annotate_paper_results.py`
(300 lines). Key properties:

- **Run-log parser**: extracts `{title_prefix_40: {status, source}}`
  from `run.log` without depending on Phase 3's merged_authors.jsonl
  being present. Handles `[PDF OK] tier (N KB):`, `[PDF缓存]`, and
  `[PDF失败]` (skipping the `^^ 上述 trace 属于:` re-emission line and
  `>>`-prefixed trace replays).
- **Column schema in sheet "后"** (9 cols, 3 PDF_Download for 3
  runs): writes to **col F** (the previously-empty 3rd slot), header
  retitled to `PDF下载(本次 2026-04-21)` for clarity. col G
  (PDF_Source) overwritten with the fresh run's tier; cache hits
  show the literal string "cache".
- **Color palette**:
  - `FF4ADE80` bright green  -- fresh download this run
  - `FFBBF7D0` light green   -- cache hit (still good)
  - `FFFCA5A5` pink/red      -- terminal failure
  - `FFE5E7EB` gray          -- paper not in run.log (self-cite skip)
  - `FF86EFAC` medium green  -- annotation row background
- **Annotation row insertion**: uses `ws.insert_rows(r+1)` bottom-up
  (iterate the original row range, collect actions, apply in
  reverse) so the insertions don't shift indices out from under us.
  Annotation row fills ALL 9 cols with COLOR_ANNOT and puts the
  text `    ↳ 本次新增下载来源: <tier>` (U+21B3 curved arrow) in
  column A with italic font.
- **Idempotent**: on re-run, deletes any row whose col-A starts with
  the annotation prefix before re-emitting. Multiple invocations
  give the same final state, not a 2×/3× accumulation of rows.
- **CLI**: `--xlsx <path>`, `--sheet <name>`, `--run <dir>` (auto-
  detects latest `result-*` with run.log if omitted).

**First application** (on the 81/116 run-log from
`result-20260421_012144`): 17 fresh downloads, 63 cache hits, 35
failures, 7 unknown. Sheet grew from 126 to 143 rows (+17
annotation rows, one per fresh download). No tests added -- the
script is a one-off post-processing tool, and the manual smoke
check (spreadsheet opens in Excel with the expected coloring and
annotation rows) is sufficient verification.

**Side observation** surfaced by the new coloring: 23 papers
regressed between col E (previous run) and col F (this run). 13 of
them had `CDP-Elsevier` as their previous source -- consistent with
tonight's V-API outage + no manual Turnstile clicks. A few others
(arXiv / gs_pdf) regressed because stale cache entries were
invalidated by the mojibake / title-match guards introduced on
2026-04-19 / 2026-04-20. Not fixed in this session but visible
at a glance in the re-colored spreadsheet.

Status: annotator script CLOSED; expected to be re-run after each
pipeline run to keep the comparison sheet fresh.

### 2026-04-20 -- Phase 2 login stamp sentinel + probe Cloudflare detection

Two related improvements to the Phase 2 login flow surfaced during
the 2026-04-20 13-paper harness validation:

1. **Returning users waste 180s on a login checkpoint they don't
   actually need.** If the same user runs the pipeline twice in a day,
   the second run shouldn't re-pop 5 browser tabs they already know
   about -- the cookies from the first run are still valid.
2. **Probe falsely reports AUTH_OK when Cloudflare is showing a
   challenge page.** Observed live: Elsevier probe returned
   `AUTH_OK` with `title='请稍候…'` (Cloudflare's "Just a moment..."
   interstitial, NOT the real Elsevier page). Downstream download
   path then blocked for 120s on the same Cloudflare challenge -- the
   probe's green signal was actively misleading.

---

**Fix 1: `phase2_login_stamp.json` sentinel** (`task_executor.py` +
`config_manager.py` + `main.py`).

- New config field `phase2_login_stamp_hours: int = Field(default=24)`.
  `0` = disable sentinel (always prompt). Silent-wipe-protected.
- Three new `TaskExecutor` helpers:
  - `_phase2_stamp_path()` -> `DEBUG_BROWSER_PROFILE_DIR /
    phase2_login_stamp.json` (inherits the 2026-04-20 absolute-path fix).
  - `_phase2_stamp_is_fresh(ttl_hours)` -> `(is_fresh, data, age_hours)`.
    Returns `(False, None, None)` on missing/corrupt file or `ttl=0`.
  - `_phase2_stamp_write(outcome, urls)` persists
    `{timestamp, outcome, urls}` JSON. Never raises -- stamp-write
    failure is logged as warning, pipeline continues.
- `_prompt_phase2_login` gets a new "step 0" short-circuit: after
  browser ensure, before tab-open, check the stamp. If fresh, log
  the short-circuit line and skip straight to the probe (still
  refresh the stamp after a successful probe so the TTL rolls
  forward for long-running sessions).
- Outcome values: `user_confirmed` (user clicked 继续),
  `timeout` (checkpoint expired with no input -- still persisted
  because cookies are assumed valid for the session), `probe_pass`
  (short-circuited AND probe then passed -- TTL refresh).

**Fix 2: `STATUS_CAPTCHA` in `cdp_login_probe`**
(`citationclaw/core/cdp_login_probe.py`).

- New status constant `STATUS_CAPTCHA = "CAPTCHA"` with icon
  `[CAPTCHA]`. DELIBERATELY NOT in `PASSING_STATUSES` because a
  challenge-blocked page means the real download will hang even if
  session cookies are valid.
- Two new module-level tuples of markers:
  - `_CAPTCHA_TITLE_MARKERS`: `"just a moment"`, `"请稍候"`,
    `"checking your browser"`, `"attention required"`,
    `"verify you are human"`, `"access denied"`,
    `"access to this page has been denied"` (covers Cloudflare
    default + Cloudflare zh-CN + Sucuri + Cloudflare Turnstile
    variant + Akamai + PerimeterX).
  - `_CAPTCHA_URL_MARKERS`: `"cdn-cgi/challenge-platform"`,
    `"/cdn-cgi/l/chk_jschl"`, `"_cf_chl_opt"` (covers the Cloudflare
    challenge iframe host pattern).
- `_probe_one` gains a `looks_like_captcha` branch that fires
  BEFORE the existing `login_wall` / `fixture_broken` branches --
  ordering is critical because a Cloudflare challenge page has a
  title that doesn't match any of those yet means "real publisher
  content not actually served yet".
- Detail message explicitly warns the user: *"the real download
  path will block on this until you solve the challenge in the
  browser"*. Much more actionable than the old misleading
  `AUTH_OK` ever was.

**Tests** (`test/test_pdf_downloader.py`, 12 new):
- `TestCdpLoginProbe.test_probe_exposes_captcha_status` -- constant
  exists, has icon, is NOT in `PASSING_STATUSES`, `r.passed` returns
  False for a CAPTCHA result.
- `TestCdpLoginProbe.test_probe_captcha_markers_cover_known_challenge_pages`
  -- marker tuple must contain the critical strings we rely on
  (including the Chinese "请稍候" that motivated this work).
- `TestCdpLoginProbe.test_probe_body_detects_cloudflare_title_over_login`
  -- source-inspection guard that `STATUS_CAPTCHA` appears in
  `_probe_one` BEFORE `STATUS_LOGIN_WALL` (ordering invariant --
  a refactor that reorders these would silently regress Elsevier
  cases to AUTH_OK + 120s download hang).
- `TestPhase2LoginStamp` class (new, 9 tests):
  - `test_config_has_phase2_login_stamp_hours`: default=24 + in
    ConfigUpdate schema.
  - `test_stamp_helpers_exist_on_task_executor`: 3 helper names.
  - `test_stamp_fresh_returns_false_when_missing`: absent file path.
  - `test_stamp_fresh_returns_true_within_ttl`: synthetic 1h-old
    stamp, assert `0.9 < age < 1.1`.
  - `test_stamp_fresh_returns_false_when_stale`: synthetic 48h-old
    stamp, TTL=24h.
  - `test_stamp_ttl_zero_always_returns_false`: escape-hatch path.
  - `test_stamp_fresh_handles_corrupt_json_gracefully`: invalid
    JSON file -> treated as absent, no raise.
  - `test_stamp_write_creates_file_with_correct_schema`: roundtrip.
  - `test_prompt_phase2_login_short_circuits_via_stamp`: source-
    inspection ordering guard -- `_phase2_stamp_is_fresh` call must
    come before the `opened = _cdp_open_login_pages` line.
- Full suite: **101 passed** (was 89). Pre-existing
  `test_citing_description_cache` async-loop failure unchanged.

**Live probe re-verification**: ran the refactored CLI against the
same port 9222 that's been up since earlier today. 5 publishers, all
landing pages loaded cleanly, no Cloudflare challenge hit during
this window (Cloudflare issues challenges dynamically; couldn't
force one). Unit tests cover the CAPTCHA code path; live validation
will come organically the next time Cloudflare fires on Elsevier.

**Expected UX**:
- First run of the day: full 180s checkpoint as before. User logs in,
  stamp gets written.
- Second run same day: `[Phase2登录] 1.2h 前已完成过检查点
  (outcome=user_confirmed)，跳过 tab 弹出 + 等待 (sentinel TTL 24h)`.
  Pipeline goes straight to probe (~45s) and then PDF download. No
  180s dead wait.
- Second-day run (>24h later): full checkpoint again (stamp expired).
- Elsevier Cloudflare case: instead of `[AUTH OK]` lying to the user,
  they'll see
  `[elsevier ] [CAPTCHA]  (8.3s)  Cloudflare/Akamai challenge page
  (title='请稍候…') -- the real download path will block on this
  until you solve the challenge in the browser`.

**Status**: COMPLETE. Server restart required for the new
`_prompt_phase2_login` logic + sentinel helpers to take effect in
an already-running FastAPI process.

**Next step**: remaining OPEN items from today's TODO review --
V-API `Connection error` diagnosis, Elsevier CDP tier pdfDownload
extraction, CORE API key UI field.

### 2026-04-20 -- TODO #3: UI front-end path verification + `__main__.py` GBK fix

Validated the UI front-end path that was newly wired today
(phase2LoginModal + `ws.on('phase2_login_prompt')` handler +
`/api/task/phase2-login-ready` endpoint). Two concrete outputs:

**Fix: `citationclaw/__main__.py` GBK encoding crash on banner print**

Attempting to start the FastAPI server (`python -m citationclaw
--no-browser`) crashed before `uvicorn.run`:
```
UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f99e'
in position 20: illegal multibyte sequence
```
The banner `f"\n  CitationClaw v2 🦞  →  http://..."` tripped
Windows GBK console. Same bug class as 2026-04-04 `[PDF✓]` and
2026-04-18 `_best_effort_utf8_console()` in `log_manager.py`, but
the logger's guard only fires AFTER `uvicorn.run` imports
`log_manager` -- too late for the banner.

- Added an identical `sys.stdout.reconfigure(encoding='utf-8',
  errors='replace')` + same for stderr at the TOP of `__main__.py`,
  immediately after imports. Silently no-ops on non-reconfigurable
  streams.
- Server now starts cleanly on GBK Windows. Verified: banner prints,
  `/api/task/status` returns 200 in 2.5 ms, port 8000 listens.

**New tests** (`test/test_pdf_downloader.py`, new `TestPhase2UiWiring`
class, 4 tests):

- `test_index_html_contains_phase2_login_modal`: GET `/` via
  `TestClient`, verify the rendered HTML body contains
  `id="phase2LoginModal"`, `id="p2l-btn-continue"`,
  `id="p2l-btn-skip"`, `id="p2l-countdown"`, `id="p2l-url-list"`.
  Catches any future template edit that accidentally removes a DOM
  ID the JS handler depends on.
- `test_main_js_has_phase2_login_prompt_handler`: GET
  `/static/js/main.js`, verify it contains `ws.on('phase2_login_prompt'
  ...` AND all 5 DOM IDs from the modal. Locks the JS<->HTML
  contract.
- `test_event_name_matches_between_server_and_frontend`: the WS
  event name `"phase2_login_prompt"` is the glue between
  `TaskExecutor._prompt_phase2_login`'s `broadcast_event(...)` call
  and `main.js`'s `ws.on(...)` handler. If one side renames it and
  the other doesn't, the modal silently never pops despite every
  log line looking fine. Test reads both files and asserts both
  contain the exact literal. Also verifies the payload keys
  (`urls`, `wait_seconds`, `cdp_port`) are all passed since the
  JS handler needs them.
- `test_phase2_login_ready_endpoint_success_path`: complements the
  existing "no-event armed -> 400" test. Arms a synthetic
  `asyncio.Event` on `task_executor`, POSTs the endpoint, verifies
  200 + `event.is_set()` afterwards. Full round-trip of the
  unlock mechanism the modal's 继续 button depends on.
- Tried a live WS integration test first (`TestClient` +
  `websocket_connect` + trigger `log_manager.broadcast_event` from
  sync test context) but it failed: broadcast relies on
  `asyncio.create_task` from a running loop, which a sync
  TestClient thread doesn't have. The test got caught by the
  `_schedule_broadcast`'s `except RuntimeError: pass` fallback and
  the event never actually went out. Source-inspection approach
  (above) is the pragmatic alternative.

**Live server verification**:
- Started `python -m citationclaw --no-browser --port 8000`
- Live HTTP:
  - `GET /` -> 200, 74 KB, 5 matches for phase2LoginModal + buttons
  - `GET /static/js/main.js` -> 200, 74 KB, 7 matches for handler +
    DOM IDs
  - `POST /api/task/phase2-login-ready` (no event) -> 400 ✓
- Live WebSocket: connected via `websocket-client`, received initial
  `{type: "history", data: []}` frame, closed cleanly.
- `GET /api/config` confirms all Phase 2 fields round-trip:
  ```
  enable_phase2_login_checkpoint = True
  enable_phase2_login_probe = True
  phase2_login_stamp_hours = 24
  phase2_login_wait_seconds = 180
  cdp_debug_port = 9222
  ```
- Server terminated cleanly, port 8000 released.

**Full suite**: **105 passed** (was 101).

**What's still NOT automatically verified** (require real user /
real pipeline run):
- Modal actually RENDERS in a browser when the event arrives
  (Bootstrap lifecycle, CSS, etc.) -- requires opening
  `http://127.0.0.1:8000` in Chrome and kicking off a pipeline.
- A real `phase2_login_prompt` WS event reaches a browser client --
  requires a real Phase 1+2 pipeline run and a connected browser.

Neither is a regression in today's work; both are the kind of
"eyeballs on the page" verification that's outside the scope of
the automated test suite. The unit + source-inspection tests above
cover every programmatic invariant that could silently drift.

**Status**: UI front-end path CLOSED as far as automation can
reach. End-user smoke-test instructions for the eyeballs-required
bits:
1. `python -m citationclaw --port 8000` (banner should print cleanly)
2. Open `http://127.0.0.1:8000`
3. Paste a Google Scholar paper title, click 开始分析
4. At the top of Phase 2 (~30s after start), verify the blue
   "Phase 2 · 出版商登录检查点" modal pops up, with 5 publisher
   URLs listed, a countdown that ticks down from 180, and two
   buttons 已登录，继续 Phase 2 / 跳过，直接继续
5. Click either button; log panel should show
   `[Phase2登录] 用户已确认登录完成` followed by the
   `[Phase2验证]` probe output (5 lines, ~50s)
6. No red errors in devtools console

---

## Conventions

- Log messages: Chinese with English technical terms
- Source labels: _SOURCE_LABELS dict in pdf_downloader.py
- PDF validity: `data[:5] == b"%PDF-"` and `len(data) > 1000`
- Cache key: MD5(DOI or title) -> {hash}.pdf in data/cache/pdf_cache/
- Config: pydantic AppConfig in config_manager.py
- Tests: test/ directory, pytest
- **Run logs**: `data/result-TIMESTAMP/run.log` (persistent, UTF-8)
- **CDP browser**: Chrome first on Windows, launched with `--proxy-bypass-list` for publisher domains
- **Dev history**: auto-synced via `.claude/skills/dev-history-sync` after every code/config/test change. Entries older than ~1 week that got archived live in `CLAUDE.ARCHIVE.md` (read-only; never append there).
