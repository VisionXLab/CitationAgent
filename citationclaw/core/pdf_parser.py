"""PDF citation context parser using PyMuPDF (fitz).

Extracts paragraphs containing citations to the target paper.
Supports both [N] and (Author, Year) citation formats.
Tags each paragraph with its section before searching.
"""
import re
from pathlib import Path
from typing import Optional, List, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

SECTION_PATTERNS = [
    (r'(?:^|\n)\s*\d*\.?\s*(Abstract)', "Abstract"),
    (r'(?:^|\n)\s*\d*\.?\s*(Introduction)', "Introduction"),
    (r'(?:^|\n)\s*\d*\.?\s*(Related\s+Work)', "Related Work"),
    (r'(?:^|\n)\s*\d*\.?\s*(Background)', "Background"),
    (r'(?:^|\n)\s*\d*\.?\s*(Preliminar(?:y|ies))', "Preliminaries"),
    (r'(?:^|\n)\s*\d*\.?\s*(Method(?:ology|s)?)', "Method"),
    (r'(?:^|\n)\s*\d*\.?\s*(Approach)', "Method"),
    (r'(?:^|\n)\s*\d*\.?\s*((?:Proposed\s+)?(?:Framework|Model|System))', "Method"),
    (r'(?:^|\n)\s*\d*\.?\s*(Experiment(?:s|al)?(?:\s+(?:Setup|Results))?)', "Experiments"),
    (r'(?:^|\n)\s*\d*\.?\s*((?:Evaluation|Analysis))', "Experiments"),
    (r'(?:^|\n)\s*\d*\.?\s*(Results?\s*(?:and\s+)?(?:Discussion)?)', "Results"),
    (r'(?:^|\n)\s*\d*\.?\s*(Discussion)', "Discussion"),
    (r'(?:^|\n)\s*\d*\.?\s*(Conclusion(?:s)?)', "Conclusion"),
    (r'(?:^|\n)\s*\d*\.?\s*(Acknowledg(?:e?ment|ment)s?)', "Acknowledgements"),
    (r'(?:^|\n)\s*\d*\.?\s*(References|Bibliography|REFERENCES)', "References"),
]


def _extract_first_author_surname(authors: List[dict]) -> str:
    """Extract surname of the first author for citation matching."""
    if not authors:
        return ""
    name = authors[0].get("name", "") if isinstance(authors[0], dict) else str(authors[0])
    name = name.strip()
    if not name:
        return ""
    # Chinese name: first char is surname
    if any('\u4e00' <= c <= '\u9fff' for c in name):
        return name[0]
    # Western name: last word is surname
    parts = name.split()
    return parts[-1] if parts else ""


class PDFCitationParser:
    """Parse PDF and find paragraphs citing the target paper.

    Improvements over v1:
    - Supports both [N] and (Author, Year) citation formats
    - Section tagging upfront (not after-the-fact detection)
    - Context window: includes surrounding paragraphs for fuller context
    - Better paragraph splitting (handles PyMuPDF line break artifacts)
    """

    def extract_citation_contexts(
        self,
        pdf_path: Path,
        target_title: str,
        target_authors: List[dict],
        target_year: Optional[int] = None,
        context_window: int = 1,
    ) -> List[dict]:
        """Parse PDF and find paragraphs citing the target paper.

        Args:
            pdf_path: Path to PDF file
            target_title: Title of the target (cited) paper
            target_authors: Authors of the target paper [{name, affiliation}]
            target_year: Publication year of the target paper
            context_window: Number of surrounding paragraphs to include (default 1)

        Returns: [{section, text, source, match_type}]
        """
        if fitz is None:
            return []
        if not pdf_path or not pdf_path.exists():
            return []

        # Suppress MuPDF C-level stderr noise (zlib errors from corrupted PDFs)
        import os, sys
        stderr_fd = sys.stderr.fileno()
        old_stderr = os.dup(stderr_fd)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, stderr_fd)
        try:
            doc = fitz.open(str(pdf_path))
            full_text = "\n".join(page.get_text() for page in doc)
            doc.close()
        finally:
            os.dup2(old_stderr, stderr_fd)
            os.close(old_stderr)
            os.close(devnull)

        return self.extract_from_text(
            full_text, target_title, target_authors, target_year, context_window
        )

    def extract_from_text(
        self,
        full_text: str,
        target_title: str,
        target_authors: List[dict],
        target_year: Optional[int] = None,
        context_window: int = 1,
    ) -> List[dict]:
        """Extract citation contexts from pre-parsed text (MinerU or PyMuPDF).

        This is the core method — can be called with text from any source.
        """
        if not full_text or not target_title:
            return []

        first_surname = _extract_first_author_surname(target_authors)

        # Step 1: Split body from references
        body_text, ref_text = self._split_references(full_text)

        # Step 2: Find reference ID [N] in references section
        ref_id = self._find_reference_id(ref_text, target_title, target_authors)

        # Step 2b: Find (Author, Year[a/b]) citation key for author-year format papers
        author_year_key = self._find_author_year_key(
            ref_text, target_title, target_authors, target_year
        )

        # Step 3: Build section-tagged paragraphs from body
        tagged_paras = self._tag_paragraphs_with_sections(body_text)

        # Step 4: Find all paragraphs containing citations to target
        hit_indices = self._find_citing_paragraphs(
            tagged_paras, ref_id, target_title, first_surname, target_year,
            author_year_key=author_year_key,
        )

        if not hit_indices:
            # Step 4b: Fallback — collect candidate paragraphs for LLM-based extraction
            # When rule-based matching fails (e.g., author-year format without known year,
            # unusual citation style, or title with special characters), provide the LLM
            # with the reference entry + any paragraphs mentioning author surnames
            fallback = self._build_llm_fallback(
                tagged_paras, ref_text, target_title, target_authors, first_surname
            )
            if fallback:
                return fallback
            return []

        # Step 5: Expand context window and deduplicate
        expanded = set()
        for idx in hit_indices:
            for offset in range(-context_window, context_window + 1):
                neighbor = idx + offset
                if 0 <= neighbor < len(tagged_paras):
                    expanded.add(neighbor)

        # Build results in order
        results = []
        for idx in sorted(expanded):
            section, text = tagged_paras[idx]
            match_type = "direct" if idx in hit_indices else "context"
            results.append({
                "section": section,
                "text": text.strip(),
                "source": "pdf",
                "match_type": match_type,
            })

        return results

    def _split_references(self, text: str) -> Tuple[str, str]:
        """Split full text into body and references section."""
        match = re.search(
            r'(?:^|\n)\s*(?:\d+[\.\s]*)?(?:References|Bibliography|REFERENCES)\s*\n',
            text, re.MULTILINE
        )
        if match:
            return text[:match.start()], text[match.start():]
        return text, ""

    @staticmethod
    def _merge_ref_entries(ref_text: str) -> List[Tuple[Optional[str], str]]:
        """Merge multi-line reference entries into single strings.

        Returns: [(ref_id_or_None, full_entry_text), ...]
        e.g., [("[1]", "Smith J. et al. Title of paper..."), ("[2]", "...")]
        or [("1", "Smith J. et al. ..."), ...] for "1." format
        """
        if not ref_text:
            return []

        lines = ref_text.split('\n')
        entries: List[Tuple[Optional[str], str]] = []
        current_id: Optional[str] = None
        current_lines: List[str] = []

        # Skip "References" / "Bibliography" header line
        header_pat = re.compile(r'^\s*(?:\d+[\.\s]*)?(?:References|Bibliography|REFERENCES)\s*$', re.IGNORECASE)
        lines = [l for l in lines if not header_pat.match(l.strip())]

        # Detect reference entry boundary patterns
        # Pattern 1: [N] — e.g., "[1] Smith, J. ..."
        # Pattern 2: N. — e.g., "1. Smith, J. ..."
        # Pattern 3: (N) — e.g., "(1) Smith, J. ..."
        bracket_pat = re.compile(r'^\s*\[(\d+)\]')
        dot_pat = re.compile(r'^\s*(\d+)\.\s+[A-Z]')
        paren_pat = re.compile(r'^\s*\((\d+)\)')

        def _flush():
            if current_lines:
                text = ' '.join(l.strip() for l in current_lines if l.strip())
                if text:
                    entries.append((current_id, text))

        # First pass: try to detect numbered format
        has_numbered = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if bracket_pat.match(stripped) or dot_pat.match(stripped) or paren_pat.match(stripped):
                has_numbered = True
                break

        if has_numbered:
            # Parse numbered entries (may span multiple lines)
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue

                new_id = None
                m = bracket_pat.match(stripped)
                if m:
                    new_id = f"[{m.group(1)}]"
                else:
                    m = dot_pat.match(stripped)
                    if m:
                        new_id = m.group(1)
                    else:
                        m = paren_pat.match(stripped)
                        if m:
                            new_id = m.group(1)

                if new_id is not None:
                    _flush()
                    current_id = new_id
                    current_lines = [stripped]
                else:
                    current_lines.append(stripped)

            _flush()
        else:
            # Fallback: blank-line separated entries (author-year format)
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    if current_lines:
                        text = ' '.join(current_lines)
                        entries.append((None, text))
                        current_lines = []
                else:
                    current_lines.append(stripped)
            if current_lines:
                entries.append((None, ' '.join(current_lines)))

        return entries

    def _find_reference_id(
        self, ref_text: str, target_title: str, target_authors: List[dict]
    ) -> Optional[str]:
        """Find the reference number [N] for the target paper in references section."""
        if not ref_text:
            return None

        entries = self._merge_ref_entries(ref_text)
        title_words = [w for w in target_title.lower().split() if len(w) > 2][:6]

        # Strategy 1: Match by title keywords (first 6 significant words)
        if len(title_words) >= 3:
            title_pattern = r'\W+(?:\w+\W+)*?'.join(re.escape(w) for w in title_words)
            for ref_id, entry_text in entries:
                if re.search(title_pattern, entry_text, re.IGNORECASE):
                    if ref_id and ref_id.startswith('['):
                        return ref_id
                    elif ref_id:
                        return f"[{ref_id}]"

        # Strategy 2: First author surname + partial title
        first_surname = _extract_first_author_surname(target_authors)
        if first_surname and len(title_words) >= 2:
            partial_pattern = r'\W+(?:\w+\W+)*?'.join(re.escape(w) for w in title_words[:3])
            for ref_id, entry_text in entries:
                entry_lower = entry_text.lower()
                if (first_surname.lower() in entry_lower
                        and re.search(partial_pattern, entry_text, re.IGNORECASE)):
                    if ref_id and ref_id.startswith('['):
                        return ref_id
                    elif ref_id:
                        return f"[{ref_id}]"

        # Strategy 3: Author surname + at least 2 title words
        if first_surname:
            for ref_id, entry_text in entries:
                entry_lower = entry_text.lower()
                if first_surname.lower() in entry_lower:
                    if sum(1 for w in title_words[:2] if w in entry_lower) >= 2:
                        if ref_id and ref_id.startswith('['):
                            return ref_id
                        elif ref_id:
                            return f"[{ref_id}]"

        return None

    def _tag_paragraphs_with_sections(self, body_text: str) -> List[Tuple[str, str]]:
        """Split body text into paragraphs, each tagged with its section name.

        Returns: [(section_name, paragraph_text), ...]
        """
        # Section header keywords for quick detection
        SECTION_KEYWORDS = [
            ("abstract", "Abstract"),
            ("introduction", "Introduction"),
            ("related work", "Related Work"),
            ("background", "Background"),
            ("preliminar", "Preliminaries"),
            ("methodology", "Method"), ("methods", "Method"), ("method", "Method"),
            ("approach", "Method"),
            ("framework", "Method"), ("model", "Method"),
            ("experiment", "Experiments"), ("evaluation", "Experiments"),
            ("result", "Results"),
            ("discussion", "Discussion"),
            ("conclusion", "Conclusion"),
            ("acknowledg", "Acknowledgements"),
            ("references", "References"), ("bibliography", "References"),
        ]

        def _detect_section_header(text: str) -> Optional[str]:
            """Check if text is a section header. Returns section name or None."""
            # Strip Markdown heading prefix (# ## ###) and numbering (1. 2.1 etc.)
            clean = re.sub(r'^#{1,4}\s*', '', text).strip()
            clean = re.sub(r'^\s*\d+[\.\d]*\.?\s*', '', clean).strip().lower()
            if len(clean) > 40:  # Too long to be a header
                return None
            for keyword, section in SECTION_KEYWORDS:
                if clean.startswith(keyword):
                    return section
            return None

        # Merge lines into paragraphs
        lines = body_text.split('\n')
        merged_lines = []
        current = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if current:
                    merged_lines.append(' '.join(current))
                    current = []
                continue
            # Check if this line is a section header → always start new paragraph
            # Also detect Markdown headers (# Section) from MinerU output
            if _detect_section_header(stripped) or re.match(r'^#{1,4}\s+\w', stripped):
                if current:
                    merged_lines.append(' '.join(current))
                merged_lines.append(stripped)
                current = []
                continue
            current.append(stripped)

        if current:
            merged_lines.append(' '.join(current))

        # Tag each paragraph with its current section
        tagged = []
        current_section = "Body"
        for para in merged_lines:
            # Check if this paragraph is/starts with a section header
            header = _detect_section_header(para)
            if header:
                current_section = header
                if len(para) < 50:
                    continue  # Pure header line, skip it
                # If header is embedded (e.g., "1. Introduction The transformer..."),
                # strip the header prefix from the paragraph text
                stripped = re.sub(r'^\s*\d*\.?\s*\w[\w\s]*?(?=\s[A-Z])', '', para, count=1).strip()
                if stripped and len(stripped) > 20:
                    para = stripped
            tagged.append((current_section, para))

        # Post-pass: infer section for "Body" paragraphs from content clues
        # This handles PDFs where section headers weren't detected properly
        for i, (section, text) in enumerate(tagged):
            if section != "Body":
                continue
            text_lower = text.lower()[:200]
            if any(k in text_lower for k in ["we propose", "our approach", "our method", "our framework",
                                               "we design", "we introduce our", "architecture"]):
                tagged[i] = ("Method", text)
            elif any(k in text_lower for k in ["we evaluate", "experiment", "we compare", "benchmark",
                                                 "evaluation", "ablation", "we test", "table "]):
                tagged[i] = ("Experiments", text)
            elif any(k in text_lower for k in ["related work", "prior work", "previous work",
                                                 "existing method", "literature"]):
                tagged[i] = ("Related Work", text)
            elif any(k in text_lower for k in ["in this paper", "we present", "this work",
                                                 "the main contribution", "motivation"]):
                tagged[i] = ("Introduction", text)
            elif any(k in text_lower for k in ["in conclusion", "we conclude", "in summary",
                                                 "future work"]):
                tagged[i] = ("Conclusion", text)

        return tagged

    def _find_author_year_key(
        self, ref_text: str, target_title: str, target_authors: List[dict],
        target_year: Optional[int],
    ) -> Optional[str]:
        """Find the (Author, Year) citation key for the target paper in references.

        Handles year-disambiguated keys like (Wang et al., 2022a).
        Returns the exact citation key string used in the body, or None.
        """
        if not ref_text or not target_year:
            return None

        first_surname = _extract_first_author_surname(target_authors)
        if not first_surname:
            return None

        title_words = [w for w in target_title.lower().split() if len(w) > 2][:4]
        surname_lower = first_surname.lower()
        year_str = str(target_year)

        entries = self._merge_ref_entries(ref_text)
        for _ref_id, entry_text in entries:
            entry_lower = entry_text.lower()
            if surname_lower not in entry_lower:
                continue
            if year_str not in entry_lower:
                continue
            if sum(1 for w in title_words if w in entry_lower) < min(2, len(title_words)):
                continue

            year_match = re.search(rf'({year_str}[a-z]?)', entry_lower)
            if year_match:
                exact_year = year_match.group(1)
                return f"{surname_lower}.*{exact_year}"

        return None

    def _find_citing_paragraphs(
        self,
        tagged_paras: List[Tuple[str, str]],
        ref_id: Optional[str],
        target_title: str,
        first_surname: str,
        target_year: Optional[int],
        author_year_key: Optional[str] = None,
    ) -> List[int]:
        """Find paragraph indices that contain citations to the target paper.

        Matching strategies (in priority order):
        1. Reference ID [N] match (most precise)
        2. Exact (Author, Year[a/b]) match using key from references section
        3. (Author, Year) pattern match (with same-surname safeguards)
        4. Direct title mention (partial, ≥3 consecutive words)
        """
        hits = []
        title_words = [w for w in target_title.lower().split() if len(w) > 2]

        for idx, (section, text) in enumerate(tagged_paras):
            if section == "References":
                continue  # Skip references section

            text_lower = text.lower()
            matched = False

            # Strategy 1: [N] reference ID (most precise)
            if ref_id and ref_id in text:
                # Verify it's a citation context: [N] or [N, M] or [N-M]
                num = ref_id.strip("[]")
                # Match the exact number within bracket citations
                if re.search(rf'\[(?:\d+[,\s\-]*)*{re.escape(num)}(?:[,\s\-]*\d+)*\]', text):
                    matched = True

            # Strategy 2: Exact (Author, Year[a/b]) match from references section
            # This handles disambiguation: Wang 2022a vs Wang 2022b
            if not matched and author_year_key:
                # author_year_key is like "wang.*2022a"
                if re.search(author_year_key, text_lower):
                    matched = True

            # Strategy 3: (Author, Year) pattern match with safeguards
            if not matched and first_surname and target_year:
                surname_lower = first_surname.lower()
                year_str = str(target_year)

                # Build precise patterns that require surname + year in citation context
                # Handle: (Wang et al., 2023), Wang et al. (2023), (Wang, 2023)
                # Also handle year-suffixed: (Wang et al., 2023a)
                patterns = [
                    # Inside parentheses: (Wang et al., 2023[a])
                    rf'\({surname_lower}\s+et\s+al\.?\s*,?\s*{year_str}[a-z]?\)',
                    rf'\({surname_lower}\s*,?\s*{year_str}[a-z]?\)',
                    # Outside parentheses: Wang et al. (2023[a])
                    rf'{surname_lower}\s+et\s+al\.?\s*\({year_str}[a-z]?\)',
                    rf'{surname_lower}\s*\({year_str}[a-z]?\)',
                    # With co-author: (Wang and Li, 2023)
                    rf'\({surname_lower}\s+(?:and|&)\s+\w+\s*,?\s*{year_str}[a-z]?\)',
                    rf'{surname_lower}\s+(?:and|&)\s+\w+\s*\({year_str}[a-z]?\)',
                ]

                for pat in patterns:
                    match = re.search(pat, text_lower)
                    if match:
                        # SAME-SURNAME SAFEGUARD: if the match includes a year suffix
                        # (e.g., 2022a), verify it matches OUR target paper's suffix
                        matched_text = match.group(0)
                        # Extract matched year+suffix
                        yr_match = re.search(rf'({year_str}[a-z]?)', matched_text)
                        if yr_match and author_year_key:
                            # We have a known key — verify suffix matches
                            matched_yr = yr_match.group(1)
                            # author_year_key contains the exact year like "2022a"
                            if matched_yr != year_str:
                                # Has a suffix — must match our key
                                key_yr = re.search(rf'({year_str}[a-z]?)', author_year_key)
                                if key_yr and key_yr.group(1) != matched_yr:
                                    continue  # Different paper by same author!
                        matched = True
                        break

            # Strategy 4: Direct title mention (≥3 consecutive significant words)
            # Use flexible gap pattern to handle stop words between significant words
            if not matched and len(title_words) >= 3:
                for start in range(len(title_words) - 2):
                    chunk = title_words[start:start + 3]
                    chunk_pattern = r'\W+(?:\w+\W+)*?'.join(re.escape(w) for w in chunk)
                    if re.search(chunk_pattern, text_lower):
                        matched = True
                        break

            if matched:
                hits.append(idx)

        return hits

    def _find_ref_entry_and_key(
        self, ref_text: str, target_title: str, first_surname: str,
    ) -> Tuple[str, str]:
        """Find the reference entry for the target paper and extract its citation key.

        Returns: (ref_entry_text, citation_key)
        e.g., ("Cunxin Fan et al. Interleave-VLA...", "[62]") or
              ("Fan, C. et al. (2025). Interleave-VLA...", "Fan et al., 2025")
        """
        if not ref_text:
            return "", ""

        title_words = [w for w in target_title.lower().split() if len(w) > 3][:5]
        entries = self._merge_ref_entries(ref_text)
        ref_entry = ""
        ref_id = None

        for entry_id, entry_text in entries:
            if len(entry_text) < 10:
                continue
            entry_lower = entry_text.lower()
            if sum(1 for w in title_words if w in entry_lower) >= min(3, len(title_words)):
                ref_entry = entry_text
                ref_id = entry_id
                break
            if first_surname and first_surname.lower() in entry_lower:
                if sum(1 for w in title_words if w in entry_lower) >= 2:
                    ref_entry = entry_text
                    ref_id = entry_id
                    break

        if not ref_entry:
            return "", ""

        # Extract citation key
        citation_key = ""
        if ref_id and (ref_id.startswith('[') or ref_id.startswith('(')):
            citation_key = ref_id
        elif ref_id:
            citation_key = f"[{ref_id}]"
        else:
            # Try to build author-year key
            if first_surname:
                year_match = re.search(r'((?:19|20)\d{2}[a-z]?)', ref_entry)
                if year_match:
                    citation_key = f"{first_surname} et al., {year_match.group(1)}"

        return ref_entry, citation_key

    def _build_llm_fallback(
        self,
        tagged_paras: List[Tuple[str, str]],
        ref_text: str,
        target_title: str,
        target_authors: List[dict],
        first_surname: str,
    ) -> List[dict]:
        """When rule-based matching fails, provide candidate text for LLM extraction.

        Provides the reference entry (for key identification) + body candidate paragraphs.
        The LLM uses the reference entry to identify the citation key, then finds
        that key in the body paragraphs.
        """
        results = []

        # Find reference entry and citation key
        ref_entry, citation_key = self._find_ref_entry_and_key(
            ref_text, target_title, first_surname
        )

        # Include reference entry as context for the LLM (NOT as a citation description)
        if ref_entry:
            results.append({
                "section": "References",
                "text": f"[参考文献条目] {ref_entry}",
                "source": "pdf",
                "match_type": "ref_entry",  # LLM must NOT use this as description
            })

        # Build search keys from citation_key, surnames, and title
        search_keys = set()

        # Citation key parts (e.g., "[62]" or "Fan")
        if citation_key:
            # For [N], search for the number in brackets
            m = re.match(r'\[(\d+)\]', citation_key)
            if m:
                search_keys.add(f"[{m.group(1)}]")
            else:
                # For author-year, search for surname
                if first_surname:
                    search_keys.add(first_surname.lower())

        # Author surnames
        if first_surname:
            search_keys.add(first_surname.lower())
        for a in target_authors:
            name = a.get("name", "") if isinstance(a, dict) else str(a)
            parts = name.strip().split()
            if parts:
                if any('\u4e00' <= c <= '\u9fff' for c in name):
                    search_keys.add(name.strip()[0])
                else:
                    search_keys.add(parts[-1].lower())

        # Distinctive title keywords (e.g., "Interleave-VLA")
        title_keys = set()
        stop_words = {'with', 'from', 'that', 'this', 'their', 'these', 'about',
                       'through', 'based', 'using', 'towards', 'model', 'models',
                       'learning', 'method', 'approach', 'paper', 'novel'}
        for w in target_title.split():
            w_clean = w.strip(':,.()[]')
            if len(w_clean) > 4 and w_clean.lower() not in stop_words:
                title_keys.add(w_clean.lower())
                if '-' in w_clean:
                    for part in w_clean.split('-'):
                        if len(part) > 3:
                            title_keys.add(part.lower())

        # Search body paragraphs
        for section, text in tagged_paras:
            if section == "References":
                continue
            text_lower = text.lower()

            # Match by citation key (most precise)
            has_key = any(k in text_lower if len(k) > 2 else k in text for k in search_keys)
            # Match by distinctive title words (at least 1)
            title_hits = sum(1 for w in title_keys if w in text_lower)

            if has_key or title_hits >= 1:
                results.append({
                    "section": section,
                    "text": text.strip(),
                    "source": "pdf",
                    "match_type": "llm_candidate",
                })

        # Limit candidates (ref_entry + max 12 body paragraphs)
        body_candidates = [r for r in results if r["match_type"] != "ref_entry"]
        if len(body_candidates) > 12:
            results = [r for r in results if r["match_type"] == "ref_entry"] + body_candidates[:12]

        # Must have at least 1 body candidate (not just ref_entry)
        if not any(r["match_type"] == "llm_candidate" for r in results):
            return []  # Only ref entry found, no body citations

        return results

    def _detect_section(self, text: str) -> str:
        """Legacy method — detect section from text content (fallback)."""
        for pattern, section_name in SECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return section_name
        return "Unknown"
