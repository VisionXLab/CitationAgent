# PDF Download + MinerU Parse + Cross-Validation Pipeline

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Download citing paper PDFs in parallel during Phase 2, parse with MinerU for accurate author/affiliation data, cross-validate with API results, and reuse parsed content for Phase 4 citation extraction.

**Architecture:** Enhanced PDFDownloader with 7-source waterfall (OpenAlex OA → arXiv → S2 → Unpaywall → publisher+Cookie → Sci-Hub → DOI redirect). MinerU parses each PDF into structured content_list.json + full.md. Lightweight LLM extracts authors/affiliations from first-page text blocks. Cache maps paper→PDF→parsed results. Phase 2.5 cross-validates API affiliations against PDF ground truth. Phase 4 uses parsed full.md instead of re-downloading.

**Tech Stack:** MinerU (magic-pdf), pycookiecheat, httpx, OpenAI SDK (lightweight model), PyMuPDF (fallback)

**Dependencies:** `pip install "mineru[all]" pycookiecheat`

---

## Milestone Overview

| Milestone | Tasks | Description |
|-----------|-------|-------------|
| M1 | 1-2 | Enhanced PDF downloader (7 sources + Cookie + Sci-Hub) |
| M2 | 3-4 | MinerU integration + parsed cache |
| M3 | 5-6 | LLM first-page extraction + cross-validation |
| M4 | 7 | Wire into Phase 2 parallel pipeline |
| M5 | 8 | Phase 4 reuse parsed content |

---

## Task 1: Enhanced PDF Downloader — OpenAlex OA + Sci-Hub + Cookie

**Files:**
- Modify: `citationclaw/core/pdf_downloader.py`
- Modify: `citationclaw/core/openalex_client.py` — add `best_oa_location.pdf_url` to `_parse_work`
- Test: `test/test_pdf_downloader.py`

**Step 1: Update OpenAlex `_parse_work` to extract OA PDF URL**

In `openalex_client.py`, the `search_work` response already has `best_oa_location` but `_parse_work` doesn't extract it. Add it alongside existing fields.

**Step 2: Rewrite PDFDownloader with 7-source waterfall**

New source priority (adapted from PaperRadar):
1. OpenAlex `best_oa_location.pdf_url` (free OA, very reliable)
2. arXiv direct `https://arxiv.org/pdf/{id}` (free, preprint)
3. S2 `openAccessPdf.url` (already in metadata)
4. Unpaywall API (DOI-based OA lookup)
5. Publisher page + Chrome Cookie injection (IEEE/ACM/Springer)
6. Sci-Hub (3 mirrors, DOI-based)
7. DOI redirect fallback

Key additions from PaperRadar:
- `_try_scihub()` — 3 mirror rotation with HTML parsing
- `_try_publisher_with_cookie()` — Chrome cookie injection via pycookiecheat
- `_extract_pdf_url_from_html()` — extract PDF link from publisher HTML pages

**Step 3: Add `oa_pdf_url` to OpenAlex `_parse_work` output**

Modify `_parse_work` to also query `best_oa_location`:
```python
# In the API call, add select parameter or parse from full response
oa_pdf = work.get("best_oa_location", {}) or {}
result["oa_pdf_url"] = oa_pdf.get("pdf_url", "")
```

Note: The current `_build_search_url` uses `filter=title.search` which doesn't return `best_oa_location` by default. Need to add `&select=...` or switch to include it.

**Step 4: Commit**
```bash
git commit -m "feat: enhanced PDF downloader with 7-source waterfall + Cookie + Sci-Hub"
```

---

## Task 2: PDF Download Progress + Parallel Execution in Phase 2

**Files:**
- Modify: `citationclaw/app/task_executor.py`

**Step 1: Start PDF downloads in parallel with Phase 2 metadata queries**

After Phase 2 metadata collection completes (we now have DOI + pdf_url + oa_pdf_url for each paper), immediately start parallel PDF downloads:

```python
# Right after Phase 2 metadata, before h-index enrichment:
self.log_manager.info("=" * 50)
self.log_manager.info("Phase 2 · PDF 并行下载: 多源瀑布下载施引论文 PDF")
self.log_manager.info("=" * 50)

downloader = PDFDownloader()
pdf_results = {}  # paper_title_lower → pdf_path

async def _download_one(paper, metadata):
    # Build download-friendly dict with all URL sources
    dl_paper = {
        "doi": (metadata or {}).get("doi", ""),
        "pdf_url": (metadata or {}).get("pdf_url", ""),
        "oa_pdf_url": (metadata or {}).get("oa_pdf_url", ""),
        "title": paper.get("paper_title", ""),
        "Paper_Title": paper.get("paper_title", ""),
    }
    return await downloader.download(dl_paper, log=self.log_manager.info)

# Parallel download (10 workers)
dl_sem = asyncio.Semaphore(10)
async def _dl_with_sem(paper, metadata):
    async with dl_sem:
        return await _download_one(paper, metadata)

dl_tasks = [_dl_with_sem(p, m) for p, m, _ in records_data]
dl_results = await asyncio.gather(*dl_tasks)

# Map results
for i, (paper, metadata, _) in enumerate(records_data):
    path = dl_results[i]
    if path:
        title_key = paper.get("paper_title", "").lower().strip()
        pdf_results[title_key] = path

downloaded = sum(1 for p in dl_results if p)
self.log_manager.success(f"PDF 下载完成: {downloaded}/{len(records_data)} 篇")
```

**Step 2: Log download progress with source info**

Each paper logs: `[PDF✓] arxiv下载成功 (245KB): Paper Title...`

**Step 3: Commit**
```bash
git commit -m "feat: parallel PDF download during Phase 2 with progress logging"
```

---

## Task 3: MinerU Parser Integration

**Files:**
- Create: `citationclaw/core/pdf_mineru_parser.py`
- Test: `test/test_pdf_mineru_parser.py`

**Step 1: Create MinerU wrapper**

```python
"""MinerU PDF parser — converts PDF to structured content_list.json + full.md.

Falls back to PyMuPDF if MinerU is not installed.
"""
import json
import subprocess
from pathlib import Path
from typing import Optional

class MinerUParser:
    """Parse PDF using MinerU (magic-pdf) CLI, with PyMuPDF fallback."""

    def __init__(self, output_base: Path = Path("data/cache/pdf_parsed")):
        self._output_base = output_base
        self._output_base.mkdir(parents=True, exist_ok=True)
        self._has_mineru = self._check_mineru()

    def _check_mineru(self) -> bool:
        try:
            result = subprocess.run(["magic-pdf", "--version"],
                                     capture_output=True, timeout=10)
            return result.returncode == 0
        except Exception:
            return False

    def parse(self, pdf_path: Path, paper_key: str) -> Optional[dict]:
        """Parse PDF and return structured result.

        Returns: {
            "content_list": [...],  # MinerU content blocks
            "full_md": "...",       # Full markdown text
            "first_page_blocks": [...],  # First N text blocks (for author extraction)
            "references_md": "...",      # References section text
            "source": "mineru" | "pymupdf",
        }
        """
        output_dir = self._output_base / paper_key
        if self._has_mineru:
            return self._parse_mineru(pdf_path, output_dir)
        return self._parse_pymupdf(pdf_path, output_dir)

    def _parse_mineru(self, pdf_path: Path, output_dir: Path) -> Optional[dict]:
        """Parse with MinerU CLI: magic-pdf -p input.pdf -o output_dir"""
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["magic-pdf", "-p", str(pdf_path), "-o", str(output_dir), "-m", "auto"],
                capture_output=True, timeout=120,
            )
            # Find output files
            content_list = self._find_file(output_dir, "content_list.json")
            full_md = self._find_file(output_dir, "full.md")
            if not content_list:
                return None
            with open(content_list) as f:
                blocks = json.load(f)
            md_text = full_md.read_text(encoding="utf-8") if full_md else ""
            return {
                "content_list": blocks,
                "full_md": md_text,
                "first_page_blocks": [b for b in blocks if b.get("page_idx", 99) == 0][:20],
                "references_md": self._extract_references(md_text),
                "source": "mineru",
            }
        except Exception:
            return None

    def _parse_pymupdf(self, pdf_path: Path, output_dir: Path) -> Optional[dict]:
        """Fallback: simple PyMuPDF text extraction."""
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            pages = [page.get_text() for page in doc]
            doc.close()
            full_text = "\n\n".join(pages)
            first_page = pages[0] if pages else ""
            return {
                "content_list": [],
                "full_md": full_text,
                "first_page_blocks": [{"type": "text", "text": line.strip(), "page_idx": 0}
                                       for line in first_page.split("\n") if line.strip()][:20],
                "references_md": self._extract_references(full_text),
                "source": "pymupdf",
            }
        except Exception:
            return None

    def _extract_references(self, text: str) -> str:
        """Extract References/Bibliography section from text."""
        import re
        match = re.search(r'(?:^|\n)\s*(?:References|Bibliography|REFERENCES)\s*\n',
                          text, re.MULTILINE)
        if match:
            return text[match.start():]
        return ""

    def _find_file(self, base: Path, pattern: str) -> Optional[Path]:
        """Find file matching pattern in directory tree."""
        for p in base.rglob(pattern):
            return p
        return None
```

**Step 2: Test**
```python
def test_parser_pymupdf_fallback(tmp_path):
    parser = MinerUParser(output_base=tmp_path)
    # PyMuPDF fallback works even without MinerU
    # (actual PDF parsing tested in test_pdf_parser.py)
    assert parser._extract_references("Some text\nReferences\n[1] Paper A") != ""
```

**Step 3: Commit**
```bash
git commit -m "feat: MinerU PDF parser with PyMuPDF fallback"
```

---

## Task 4: PDF Parsed Cache

**Files:**
- Create: `citationclaw/core/pdf_parse_cache.py`
- Test: `test/test_pdf_parse_cache.py`

**Step 1: Implement cache mapping paper → pdf_path → parsed results**

```python
"""Cache for PDF parsed results.

Structure:
  data/cache/pdf_parsed/
    index.json              # {paper_key: {pdf_path, parsed_at, has_authors}}
    {paper_key}/
      origin.pdf            # symlink or copy
      content_list.json     # MinerU output
      full.md               # Markdown
      authors.json          # LLM-extracted authors
      meta.json             # {title, doi, source, parsed_at}
"""
```

Key methods:
- `get(paper_key) → Optional[dict]` — return cached parsed result
- `store(paper_key, parsed_result, meta)` — persist to disk
- `has_parsed(paper_key) → bool` — check if already parsed

**Step 2: Commit**
```bash
git commit -m "feat: PDF parse cache with index.json mapping"
```

---

## Task 5: LLM First-Page Author Extraction

**Files:**
- Create: `citationclaw/core/pdf_author_extractor.py`
- Create: `citationclaw/config/prompts/pdf_author_extract.txt`
- Test: `test/test_pdf_author_extractor.py`

**Step 1: Create prompt template**

```
以下是一篇学术论文首页的文本块（来自 PDF 解析，按排版顺序排列）：

{first_page_text}

请从中提取所有作者及其单位信息。

要求：
1. 只提取论文首页中明确列出的作者和单位
2. 注意区分作者名和其他文本（如标题、摘要）
3. 如果有对应的邮箱，也请提取

以 JSON 数组格式输出：
[
  {{"name": "作者全名", "affiliation": "所在机构全称", "email": ""}}
]

如果无法提取，输出空数组 []
```

**Step 2: Implement extractor using lightweight LLM**

```python
class PDFAuthorExtractor:
    """Extract authors + affiliations from PDF first page using lightweight LLM."""

    async def extract(self, first_page_blocks: list, api_key, base_url, model) -> list:
        """Send first-page text to lightweight LLM, return author list."""
        # Build text from blocks
        text = "\n".join(f"[{i}] {b.get('text','')}" for i, b in enumerate(first_page_blocks))
        # Call LLM
        prompt = PromptLoader().render("pdf_author_extract", first_page_text=text)
        # Parse JSON response
        return authors_list
```

**Step 3: Commit**
```bash
git commit -m "feat: LLM-based author extraction from PDF first page"
```

---

## Task 6: Cross-Validation — PDF vs API Affiliations

**Files:**
- Create: `citationclaw/core/affiliation_validator.py`
- Test: `test/test_affiliation_validator.py`

**Step 1: Implement cross-validation logic**

```python
class AffiliationValidator:
    """Cross-validate API affiliations against PDF-extracted ground truth.

    Strategy:
    - Match authors by name (fuzzy, handles Chinese/English variants)
    - If PDF has affiliation and API doesn't → use PDF's
    - If both have but differ → prefer PDF (publication-time truth)
    - If only API has → keep API's (better than nothing)
    """

    def validate(self, api_authors: list, pdf_authors: list) -> list:
        """Merge and validate author data from both sources."""
        ...
```

Key matching logic:
- Name normalization (same as scholar dedup: extract Chinese + English variants)
- Affiliation preference: PDF > OpenAlex Author API > OpenAlex paper-level > empty

**Step 2: Commit**
```bash
git commit -m "feat: cross-validate author affiliations between API and PDF"
```

---

## Task 7: Wire into Phase 2 Pipeline

**Files:**
- Modify: `citationclaw/app/task_executor.py`

**Step 1: Add Phase 2 sub-stages**

After current Phase 2 metadata + h-index enrichment, add:
```
Phase 2 · PDF下载 (并行10路)
Phase 2 · MinerU解析 (并行，下载一个解析一个)
Phase 2 · 作者交叉验证 (PDF机构 vs API机构)
```

**Step 2: Stream processing — download+parse pipeline**

Instead of download-all-then-parse-all, use streaming:
```python
async def _download_and_parse_one(paper, metadata, downloader, parser, extractor):
    # Download
    pdf_path = await downloader.download(paper)
    if not pdf_path:
        return None
    # Parse
    parsed = parser.parse(pdf_path, paper_key)
    if not parsed:
        return None
    # Extract authors from first page
    pdf_authors = await extractor.extract(parsed["first_page_blocks"], ...)
    return {"pdf_path": pdf_path, "parsed": parsed, "pdf_authors": pdf_authors}
```

**Step 3: Cross-validate and update records**

```python
validator = AffiliationValidator()
for paper, metadata, canonical in records_data:
    pdf_result = pdf_results.get(title_key)
    if pdf_result and pdf_result.get("pdf_authors"):
        validated = validator.validate(
            api_authors=metadata.get("authors", []),
            pdf_authors=pdf_result["pdf_authors"],
        )
        metadata["authors"] = validated  # Replace with validated data
```

**Step 4: Commit**
```bash
git commit -m "feat: Phase 2 parallel PDF download + parse + cross-validation"
```

---

## Task 8: Phase 4 Reuse Parsed Content

**Files:**
- Modify: `citationclaw/skills/phase4_citation_extract.py`

**Step 1: Use cached MinerU full.md instead of re-downloading**

The Phase 4 citation extraction skill currently tries to download PDF and parse it. With the new cache, it should:
1. Check pdf_parse_cache for existing parsed content
2. If available, use `full_md` + `references_md` directly
3. If not, fall back to current download+parse logic

**Step 2: Use `references_md` for more accurate citation context**

MinerU's parsed references section is much more structured than raw PyMuPDF text. The LLM receives cleaner input for citation description extraction.

**Step 3: Commit**
```bash
git commit -m "feat: Phase 4 reuses MinerU parsed cache, better citation extraction"
```

---

## Execution Order

```
Task 1 (Enhanced downloader) ─────┐
Task 2 (Parallel download)        │
                                   ├──→ Task 7 (Wire into Phase 2)
Task 3 (MinerU parser)            │          ↓
Task 4 (Parse cache)              │     Task 8 (Phase 4 reuse)
Task 5 (LLM author extraction)   │
Task 6 (Cross-validation)  ───────┘
```

Tasks 1-6 are independent components. Task 7 integrates them. Task 8 leverages the cache.
