"""Bridge between new structured-API outputs and legacy export format."""
import json
from typing import Optional, List
from citationclaw.core.scholar_search_agent import ScholarSearchAgent


def _format_pdf_failures(failures) -> str:
    """Serialize PDF download stage failures into a compact audit string."""
    if not failures or not isinstance(failures, list):
        return ""
    parts = []
    for f in failures:
        if not isinstance(f, dict):
            continue
        stage = f.get("stage", "?")
        bits = []
        for k in ("http_status", "error_type", "reason"):
            v = f.get(k)
            if v is not None and v != "":
                bits.append(f"{k}={v}")
        parts.append(f"{stage}:" + ",".join(bits) if bits else stage)
    return "; ".join(parts)


class PipelineAdapter:
    """Convert between new pipeline data and legacy record format."""

    def flatten_phase1_line(self, line_data: dict) -> list:
        """Flatten one Phase 1 JSONL line (page-based) into individual papers."""
        papers = []
        for page_id, page_content in line_data.items():
            paper_dict = page_content.get("paper_dict", {})
            for paper_id, paper_info in paper_dict.items():
                papers.append({
                    "page_id": page_id,
                    "paper_id": paper_id,
                    "paper_title": paper_info.get("paper_title", ""),
                    "paper_link": paper_info.get("paper_link", ""),
                    "paper_year": paper_info.get("paper_year"),
                    "citation": paper_info.get("citation", "0"),
                    "authors_raw": paper_info.get("authors", {}),
                    "gs_pdf_link": paper_info.get("gs_pdf_link", ""),
                    "gs_all_versions": paper_info.get("gs_all_versions", ""),
                })
        return papers

    def flatten_phase1_file(self, file_path) -> list:
        """Read Phase 1 JSONL file and flatten all pages into paper list."""
        all_papers = []
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                all_papers.extend(self.flatten_phase1_line(data))
        return all_papers

    def to_legacy_record(
        self,
        paper: dict,
        metadata: Optional[dict],
        self_citation: dict,
        renowned_scholars: list,
        citing_paper: str,
        record_index: int,
        api_authors_snapshot: Optional[list] = None,
        pdf_authors_snapshot: Optional[list] = None,
        pdf_downloaded: bool = False,
        pdf_path: str = "",
    ) -> dict:
        """Convert new pipeline data into legacy {index: record} format.

        This produces the exact format Phase 3 Export expects:
          {"1": {"Paper_Title": ..., "Searched Author-Affiliation": ..., ...}}

        api_authors_snapshot / pdf_authors_snapshot: original data before merge,
        for transparency in Excel output.
        """
        authors = (metadata or {}).get("authors", [])
        sources = (metadata or {}).get("sources", [])

        # Fallback: if API returned no authors, build from Google Scholar data
        if not authors and paper.get("authors_raw"):
            import re as _re
            for key in paper["authors_raw"]:
                match = _re.match(r'author_\d+_(.*)', key)
                name = match.group(1) if match else key
                if name:
                    authors.append({"name": name, "affiliation": "", "country": ""})
            if not sources:
                sources = ["scholar"]

        # Build author-affiliation string (merged/final version)
        affil_lines = []
        for a in authors:
            affil_lines.append(a.get("name", ""))
            affil = a.get("affiliation", "") or "未知机构"
            src = a.get("affiliation_source", "")
            if src == "pdf" and affil != "未知机构":
                affil = f"{affil} [PDF✓]"
            affil_lines.append(affil)
        searched_affiliation = "\n".join(affil_lines)

        # Build API-only snapshot string (before PDF validation)
        api_affil_str = ""
        if api_authors_snapshot:
            api_lines = []
            for a in api_authors_snapshot:
                api_lines.append(f"{a.get('name','')} | {a.get('affiliation','') or '未知'} | {ScholarSearchAgent._normalize_country(a.get('country',''))}")
            api_affil_str = "\n".join(api_lines)

        # Build PDF-only snapshot string
        pdf_affil_str = ""
        if pdf_authors_snapshot:
            pdf_lines = []
            for a in pdf_authors_snapshot:
                pdf_lines.append(f"{a.get('name','')} | {a.get('affiliation','') or '未知'}")
            pdf_affil_str = "\n".join(pdf_lines)

        # First author info (normalize country to Chinese, infer if missing)
        first_author = authors[0] if authors else {}
        first_inst = first_author.get("affiliation", "")
        first_country = ScholarSearchAgent._normalize_country(
            first_author.get("country", "")
        )
        # If still no country, try to infer from affiliation
        if not first_country and first_inst:
            from citationclaw.core.affiliation_validator import AffiliationValidator
            inferred = AffiliationValidator._infer_country(first_inst)
            if inferred:
                first_country = ScholarSearchAgent._normalize_country(inferred)

        # Build author info summary (merged version with all details)
        author_info_parts = []
        for a in authors:
            parts = [a.get("name", "")]
            if a.get("affiliation"):
                parts.append(f"机构: {a['affiliation']}")
            country = ScholarSearchAgent._normalize_country(a.get("country", ""))
            if country:
                parts.append(f"国家: {country}")
            if a.get("h_index"):
                parts.append(f"h-index: {a['h_index']}")
            src = a.get("affiliation_source", "")
            if src:
                parts.append(f"来源: {src}")
            author_info_parts.append(", ".join(parts))
        searched_info = "\n".join(author_info_parts)

        # Build renowned scholar fields
        renowned_text = ""
        formatted_scholars = []
        for s in renowned_scholars:
            honors_str = ", ".join(s.get("honors", []))
            renowned_text += f"{s.get('name', '')} ({s.get('tier', '')}: {honors_str})\n"
            formatted_scholars.append({
                "name": s.get("name", ""),
                "institution": s.get("affiliation", ""),
                "country": s.get("country", ""),
                "position": s.get("position", "") or s.get("tier", ""),
                "titles": honors_str,
            })

        # Authors with profile (preserve original GS format)
        authors_with_profile = json.dumps(
            paper.get("authors_raw", {}), ensure_ascii=False
        )

        # Sanitize: replace nan/None with empty string
        def _clean(val):
            s = str(val or "").strip()
            return "" if s.lower() in ("nan", "none") else s

        record = {
            # ── 核心信息（用户关注）──
            "Paper_Title": _clean(paper.get("paper_title", "")),
            "Paper_Year": paper.get("paper_year"),
            "Venue": _clean((metadata or {}).get("venue", "")),
            "Paper_Link": _clean(paper.get("paper_link", "")),
            "doi": _clean((metadata or {}).get("doi", "")),
            "Citations": _clean(paper.get("citation", "0")),
            "Citing_Paper": _clean(citing_paper),
            "Is_Self_Citation": self_citation.get("is_self_citation", False),
            # ── 作者与机构（最终合并版）──
            "Authors_Affiliation": _clean(searched_affiliation),
            "First_Author_Institution": _clean(first_inst),
            "First_Author_Country": _clean(first_country),
            # ── 知名学者 ──
            "Renowned Scholar": _clean(renowned_text),
            "Formated Renowned Scholar": formatted_scholars,
            # ── PDF 与数据来源 ──
            "PDF_Download": pdf_downloaded,
            "pdf_url": _clean((metadata or {}).get("pdf_url", "")),
            "PDF_Source": _clean(paper.get("_pdf_source", "")),
            "PDF_Failure_Reasons": _format_pdf_failures(paper.get("_pdf_failures")),
            "Data_Sources": ",".join(sources),
            # ── 调试/审计字段（隐藏在最后）──
            "API_Authors": _clean(api_affil_str),
            "PDF_Authors": _clean(pdf_affil_str),
            "PDF_Path": _clean(pdf_path),
        }
        return {str(record_index): record}
