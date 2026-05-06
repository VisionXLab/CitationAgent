# Fast Download Mode Design

Date: 2026-04-28
Status: Draft for user review

## Goal

Add a configurable fast PDF download mode for CitationClaw v2. The mode should
reduce Phase 2 wall-clock time by avoiding known slow publisher paths, while
preserving PDF correctness checks and keeping MinerU parsing enabled.

The main target is the download stage inside Phase 2. MinerU parsing, PDF author
extraction, and author cross-validation remain part of the pipeline.

## Non-Goals

- Do not skip MinerU parsing.
- Do not weaken PDF validation, title matching, mojibake detection, or cache
  correctness checks.
- Do not permanently disable ScraperAPI. ScraperAPI can be used again when the
  configured key has balance.
- Do not redesign Phase 1 scraping or Phase 3 scholar assessment in this change.

## Configuration

Add a new config field:

```text
download_mode: "full" | "fast"
```

Default value:

```text
full
```

`full` keeps current behavior. `fast` changes only the PDF download strategy and
the Phase 2 login probe behavior.

## Fast Mode Policy

Fast mode keeps low-latency, high-confidence sources:

- PDF cache
- Google Scholar sidebar PDF link
- Unpaywall
- OpenAlex OA PDF
- CVF
- Semantic Scholar openAccessPdf
- DBLP conference PDF
- arXiv by metadata ID
- arXiv title search
- OpenReview direct URLs
- CORE search, when `core_api_key` is configured
- Non-Elsevier ScraperAPI publisher paths, when ScraperAPI keys are configured
- IEEE CDP, when `cdp_debug_port` is configured and available

Fast mode skips known slow or low-yield paths:

- CDP-Elsevier / ScienceDirect
- ScraperAPI+Elsevier publisher render / pdfft path
- ScienceDirect pdfft attempts reached from transformed publisher URLs
- Full-cascade retries after an entire attempt fails
- Sci-Hub direct mirror race and Sci-Hub ScraperAPI rescue
- LLM PDF search fallback
- curl legacy publisher fallback
- ScraperAPI smart fallback for unknown non-publisher pages

Fast mode does not globally skip Elsevier papers. Elsevier papers may still
download from free or indexed alternatives such as Unpaywall, OpenAlex OA,
Semantic Scholar OA, arXiv, CORE, Google Scholar sidebar PDFs, or cached PDFs.

## ScraperAPI Behavior

ScraperAPI remains available in fast mode when a usable key is configured. The
mode only avoids Elsevier-specific ScraperAPI rendering because that path has
poor latency and often returns Cloudflare or HTML blocker pages instead of PDFs.

When a ScraperAPI key has no balance, existing failure handling should still
degrade gracefully. Fast mode should not require the user to remove keys from
config before running.

## CDP Behavior

Fast mode keeps non-Elsevier CDP paths. The current concrete download paths are:

- IEEE CDP: keep enabled.
- Elsevier CDP: skip.

The Phase 2 login checkpoint may still run when enabled, but fast mode should
skip the post-login publisher probe by default because it adds about 50 seconds
before real downloading. The user can still re-enable the probe explicitly in
full mode or by config if needed.

If future ACM, Springer, or Wiley CDP download paths are added, fast mode should
keep them by default unless they introduce human-verification waits comparable
to Elsevier.

## Retry And Timeout Behavior

Full mode keeps current retry behavior:

```text
_RETRY_ATTEMPTS = 2
_RETRY_DELAY = 8
```

Fast mode should use:

```text
retry_attempts = 0
```

This means one cascade pass per paper. The goal is to avoid repeating the same
publisher waits three times for papers that are unlikely to recover.

Per-source direct fetch timeouts can remain mostly unchanged for the first
implementation. The main speed gain comes from skipping slow classes of sources
and retries. Source-level timeout tuning can be a later improvement after
measuring fast-mode logs.

## MinerU And PDF Author Validation

MinerU remains enabled in fast mode. After a PDF is downloaded, the existing
Phase 2 flow should still:

- Parse the PDF through MinerU Cloud Agent, MinerU Cloud Precision, local
  MinerU, or PyMuPDF fallback.
- Extract first-page author and affiliation data with the lightweight LLM.
- Cross-validate PDF authors against API metadata.
- Store and reuse PDF parse cache entries.

Fast mode is therefore a faster download strategy, not a reduced-quality author
validation mode.

## Data Flow

1. UI or config sets `download_mode`.
2. `AppConfig` exposes `download_mode` with default `full`.
3. `TaskExecutor` reads `download_mode` and passes it to `PDFDownloader`.
4. `TaskExecutor` skips Phase 2 login probe when `download_mode == "fast"`.
5. `PDFDownloader.download()` uses a mode-specific policy:
   - `full`: current cascade and retries.
   - `fast`: filtered cascade and no full-cascade retry.
6. Existing result fields continue to work:
   - `PDF_Path`
   - `PDF_Source`
   - `PDF_Failure_Reasons`
7. MinerU parsing and downstream Phase 3 behavior continue unchanged.

## UI

Add a small config control for the mode. The preferred UI is a simple select or
segmented control:

```text
Full
Fast
```

The text should be minimal and operational. No in-app explanatory block is
needed. Tooltips or short labels are enough.

The quick-start / index config path and the full config modal should both round
trip `download_mode` so UI saves cannot reset it to the default.

## Error Handling

- If an Elsevier paper fails in fast mode, the failure trace should clearly show
  that Elsevier CDP / ScraperAPI publisher paths were skipped by fast mode.
- If ScraperAPI is exhausted, fast mode should log concise failure lines and
  continue to other papers.
- If a skipped path might have succeeded in full mode, the summary should make
  that visible through `PDF_Failure_Reasons` or the per-paper trace.
- Cache hits should behave identically in both modes.

## Tests

Add tests for:

- `AppConfig` default is `download_mode == "full"`.
- `ConfigUpdate` preserves and round-trips `download_mode`.
- Fast mode disables full-cascade retries.
- Fast mode skips CDP-Elsevier.
- Fast mode skips ScraperAPI+Elsevier while keeping non-Elsevier ScraperAPI
  paths eligible.
- Fast mode does not skip MinerU parsing in `TaskExecutor`.
- PDF validation behavior remains shared across full and fast modes.

## Rollout

Keep default mode as `full` to avoid surprising existing users. The first
implementation should be opt-in through config/UI. After a few gray tests, the
project can decide whether `fast` should become the recommended mode for large
batches where Elsevier waits dominate runtime.

## Decisions For The First Implementation

- Skip Sci-Hub in fast mode. The current direct mirror race can cost about 20
  seconds on failure, and the ScraperAPI rescue is not appropriate when the key
  may be out of balance.
- Skip LLM PDF search in fast mode. The current search path is optimized for
  coverage, not latency, and can wait on 90-second search-model calls plus
  upstream retry logic.
- Keep MinerU parsing enabled. Fast mode only shortens the PDF acquisition
  strategy.
- Keep non-Elsevier ScraperAPI and IEEE CDP eligible. They can still add useful
  coverage without the ScienceDirect Cloudflare wait pattern.
