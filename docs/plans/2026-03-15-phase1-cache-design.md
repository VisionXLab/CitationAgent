# Design: Phase 1 Citation Scraping — Persistent Cache with Resume

**Date:** 2026-03-15
**Status:** Approved

## Problem

Phase 1 (Google Scholar citation scraping) has no persistent cache. Every run starts from page 0, even if the same paper was fully or partially scraped before. This wastes ScraperAPI credits when a run is interrupted or when the same paper is re-searched across separate sessions.

## Non-Goals

- Changing Phase 2 / Phase 4 cache behavior (already implemented)
- Adding cache expiry (cache is permanent, user clears manually)
- Changing the scraper's output format or Phase 2's input format

## Solution: Phase1Cache with Page Callback Integration

### Cache File

`data/cache/phase1_cache.json` — permanent, manual clear, same directory as other caches.

**Structure:**
```json
{
  "https://scholar.google.com/scholar?cites=123...": {
    "url": "...",
    "complete": false,
    "mode": "normal",
    "updated_at": "2026-03-15T10:00:00",
    "papers": {
      "https://paper1.com": {
        "paper_title": "...", "paper_year": 2023,
        "citation": "Cited by 42", "authors": {...}
      },
      "paper title lowercase": { ... }
    }
  },
  "https://scholar.google.com/scholar?cites=456...": {
    "url": "...",
    "complete": false,
    "mode": "year_traverse",
    "updated_at": "2026-03-15T...",
    "years": {
      "2020": {"complete": true},
      "2021": {"complete": true},
      "2022": {"complete": false}
    },
    "papers": {
      "https://paper2.com": { ... }
    }
  }
}
```

**Dedup key:** `paper_link` if non-empty, else `paper_title.lower()` — matches `AuthorInfoCache.make_key` convention.

---

## Components

### New: `citationclaw/core/phase1_cache.py`

```python
class Phase1Cache:
    DEFAULT_FILE = Path("data/cache/phase1_cache.json")

    def is_complete(self, url: str) -> bool
    def is_year_complete(self, url: str, year: int) -> bool
    def get_missing_years(self, url: str, all_years: list[int]) -> list[int]
    async def add_papers(self, url: str, papers: dict, year: int | None = None)
    async def mark_year_complete(self, url: str, year: int)
    async def mark_complete(self, url: str)
    def build_jsonl(self, url: str) -> str   # reconstructs JSONL for Phase 2
```

- `add_papers` deduplicates before writing; saves to disk immediately (like other caches)
- `build_jsonl` produces lines of `{"page_N": {"paper_dict": {10 papers}, "next_page": null}}`
- Load failure (corrupt JSON) → `_data = {}` (silent fallback, normal scrape proceeds)

### Modify: `citationclaw/core/scraper.py`

Add optional `page_callback: Optional[Callable] = None` to `scrape()` and `_scrape_single_year()`.

After the existing page-write at line ~1228:
```python
f.write(json.dumps({f'page_{page_count}': record}, ensure_ascii=False) + '\n')
f.flush()
if page_callback:
    await page_callback(paper_dict, year=None)  # year passed in year-traverse mode
```

Default `None` → zero behavior change for all existing callers.

### Modify: `citationclaw/skills/phase1_citation_fetch.py`

**Normal mode:**
1. `if cache.is_complete(url)`: write `build_jsonl()` to `output_file`, return — skip scraper entirely
2. Otherwise: pass `page_callback=lambda pd, year: cache.add_papers(url, pd)` to `scraper.scrape()`
3. After scrape completes: `await cache.mark_complete(url)`

**Year-traverse mode:**
1. After detecting year list: call `cache.get_missing_years(url, all_years)` → only scrape missing years
2. For each year being scraped: pass `page_callback=lambda pd, year: cache.add_papers(url, pd, year)`
3. After each year completes: `await cache.mark_year_complete(url, year)`
4. After all years complete: `await cache.mark_complete(url)`

Skipped (already-complete) years: their papers are already in `cache.papers` — included automatically in `build_jsonl()`.

---

## Compatibility Guarantees

| Scenario | Behavior |
|----------|----------|
| `page_callback=None` (default) | scraper.py behavior unchanged |
| `probe_only=True` | Skip all cache logic entirely |
| Manual `start_page > 0` | Respect caller's start_page; cache still receives new pages via callback |
| Year-traverse temp file merge | Existing `_merge_year_files()` unchanged; cache intercepts at page level, not file level |
| New year detected on re-run | Scrape new year; skip cached years; no duplicate scraping |
| Cache file corrupt / missing | Fall back to normal scrape; log warning only |
| Write failure | Log warning; do not interrupt scrape |

## JSONL Reconstruction Format

`build_jsonl()` must match scraper's native output exactly so Phase 2 reads it without changes:

```json
{"page_0": {"paper_dict": {"paper_0": {…}, "paper_1": {…}, …, "paper_9": {…}}, "next_page": null}}
{"page_1": {"paper_dict": {…}, "next_page": null}}
```

- 10 papers per page (matches Google Scholar pagination)
- `next_page: null` (reconstruction needs no pagination URL)
- Paper keys: `paper_0`, `paper_1`, … (sequential within each page)

## Files to Modify

| File | Change |
|------|--------|
| `citationclaw/core/phase1_cache.py` | **New** — Phase1Cache class |
| `citationclaw/core/scraper.py` | Add optional `page_callback` to `scrape()` and `_scrape_single_year()` |
| `citationclaw/skills/phase1_citation_fetch.py` | Cache check, skip/resume logic, callbacks, mark complete |
