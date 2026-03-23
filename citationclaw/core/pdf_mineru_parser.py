"""MinerU PDF parser — converts PDF to structured content + markdown.

Uses MinerU (mineru package) Python API with PyMuPDF fallback.
"""
import json
import re
import hashlib
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone


class MinerUParser:
    """Parse PDF using MinerU, with PyMuPDF fallback."""

    def __init__(self, output_base: Path = Path("data/cache/pdf_parsed")):
        self._output_base = output_base
        self._output_base.mkdir(parents=True, exist_ok=True)
        self._has_mineru = self._check_mineru()

    @staticmethod
    def _check_mineru() -> bool:
        try:
            from mineru.cli.client import do_parse
            return True
        except ImportError:
            return False

    def paper_key(self, paper: dict) -> str:
        """Generate a stable key for a paper."""
        key = paper.get("doi") or paper.get("Paper_Title") or paper.get("title") or "unknown"
        return hashlib.md5(key.encode()).hexdigest()[:16]

    def parse(self, pdf_path: Path, paper_key: str) -> Optional[dict]:
        """Parse PDF and return structured result.

        Returns: {
            "content_list": [...],
            "full_md": "...",
            "first_page_blocks": [...],
            "references_md": "...",
            "source": "mineru" | "pymupdf",
            "parsed_at": "ISO8601",
        }
        """
        output_dir = self._output_base / paper_key

        # Check if already parsed
        cached = self._load_cached(output_dir)
        if cached:
            return cached

        if self._has_mineru:
            result = self._parse_mineru(pdf_path, output_dir)
            if result:
                return result

        # Fallback to PyMuPDF
        return self._parse_pymupdf(pdf_path, output_dir)

    def _load_cached(self, output_dir: Path) -> Optional[dict]:
        """Load previously parsed result from cache directory."""
        md_path = output_dir / "full.md"
        if not md_path.exists():
            return None
        try:
            md_text = md_path.read_text(encoding="utf-8")
            # Try to load content_list if available
            content_list = []
            for f in output_dir.rglob("*content_list.json"):
                with open(f) as fh:
                    content_list = json.load(fh)
                break
            return {
                "content_list": content_list,
                "full_md": md_text,
                "first_page_blocks": [b for b in content_list if b.get("page_idx", 99) == 0][:20] if content_list else self._md_to_first_page(md_text),
                "references_md": self._extract_references(md_text),
                "source": "mineru" if content_list else "pymupdf",
                "parsed_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception:
            return None

    def _parse_mineru(self, pdf_path: Path, output_dir: Path) -> Optional[dict]:
        """Parse with MinerU Python API."""
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            from mineru.cli.client import do_parse

            pdf_bytes = pdf_path.read_bytes()
            do_parse(
                output_dir=str(output_dir),
                pdf_file_names=[pdf_path.name],
                pdf_bytes_list=[pdf_bytes],
                p_lang_list=["en"],
                backend="pipeline",
                parse_method="txt",  # Fast text-only mode (no OCR/table/formula)
                f_dump_md=True,
                f_dump_content_list=True,
                f_dump_model_output=False,
                f_dump_orig_pdf=False,
                f_draw_layout_bbox=False,
                f_draw_span_bbox=False,
                f_dump_middle_json=False,
            )

            # Find output files
            content_list = []
            for f in output_dir.rglob("*content_list.json"):
                with open(f) as fh:
                    content_list = json.load(fh)
                break

            md_text = ""
            for f in output_dir.rglob("full.md"):
                md_text = f.read_text(encoding="utf-8")
                # Copy to standard location
                std_path = output_dir / "full.md"
                if f != std_path:
                    std_path.write_text(md_text, encoding="utf-8")
                break

            if not md_text and not content_list:
                return None

            return {
                "content_list": content_list,
                "full_md": md_text,
                "first_page_blocks": [b for b in content_list if b.get("page_idx", 99) == 0][:20],
                "references_md": self._extract_references(md_text),
                "source": "mineru",
                "parsed_at": datetime.now(timezone.utc).isoformat(),
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

            # Save to cache
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "full.md").write_text(full_text, encoding="utf-8")

            return {
                "content_list": [],
                "full_md": full_text,
                "first_page_blocks": self._md_to_first_page(first_page),
                "references_md": self._extract_references(full_text),
                "source": "pymupdf",
                "parsed_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception:
            return None

    @staticmethod
    def _md_to_first_page(text: str) -> list:
        """Convert first-page text to pseudo content blocks."""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return [{"type": "text", "text": l, "page_idx": 0} for l in lines[:20]]

    @staticmethod
    def _extract_references(text: str) -> str:
        """Extract References/Bibliography section from text."""
        match = re.search(r'(?:^|\n)\s*(?:References|Bibliography|REFERENCES)\s*\n',
                          text, re.MULTILINE)
        if match:
            return text[match.start():]
        return ""
