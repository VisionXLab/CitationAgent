"""Cross-validate author affiliations between API data and PDF-extracted data.

Strategy:
- Match authors by name (fuzzy, handles Chinese/English variants)
- PDF affiliation = publication-time truth (preferred)
- API affiliation = current affiliation (may have changed)
- Merge: PDF > API Author-level > API paper-level > empty
"""
import re
from typing import List, Optional


class AffiliationValidator:
    """Cross-validate and merge author data from API and PDF sources."""

    def validate(self, api_authors: List[dict], pdf_authors: List[dict]) -> List[dict]:
        """Merge API authors with PDF-extracted authors.

        For each API author:
        - If matched in PDF -> use PDF affiliation (publication-time truth)
        - If not matched -> keep API affiliation
        Unmatched PDF authors are appended.

        Returns: merged author list with 'affiliation_source' tag.
        """
        if not pdf_authors:
            return api_authors
        if not api_authors:
            return [{"name": a["name"], "affiliation": a.get("affiliation", ""),
                      "country": "", "affiliation_source": "pdf"}
                    for a in pdf_authors]

        # Build PDF lookup by name variants
        pdf_by_keys: dict = {}  # name_key -> pdf_author
        for a in pdf_authors:
            for key in self._name_keys(a.get("name", "")):
                if key not in pdf_by_keys:
                    pdf_by_keys[key] = a

        matched_pdf_names = set()
        merged = []
        for api_a in api_authors:
            enriched = dict(api_a)
            api_keys = self._name_keys(api_a.get("name", ""))

            # Try to find PDF match
            pdf_match = None
            for k in api_keys:
                if k in pdf_by_keys:
                    pdf_match = pdf_by_keys[k]
                    matched_pdf_names.update(self._name_keys(pdf_match.get("name", "")))
                    break

            if pdf_match:
                # PDF affiliation takes priority (publication-time truth)
                pdf_affil = pdf_match.get("affiliation", "").strip()
                if pdf_affil:
                    enriched["affiliation"] = pdf_affil
                    enriched["affiliation_source"] = "pdf"
                    # Infer country from affiliation if API didn't provide one
                    if not enriched.get("country"):
                        enriched["country"] = self._infer_country(pdf_affil)
                else:
                    enriched["affiliation_source"] = "api"
                # Also grab email if PDF has it
                if pdf_match.get("email"):
                    enriched["email"] = pdf_match["email"]
            else:
                enriched["affiliation_source"] = "api"
                # Infer country from API affiliation if missing
                if not enriched.get("country") and enriched.get("affiliation"):
                    enriched["country"] = self._infer_country(enriched["affiliation"])

            merged.append(enriched)

        # Append unmatched PDF authors (API missed them)
        for pdf_a in pdf_authors:
            pdf_keys = self._name_keys(pdf_a.get("name", ""))
            if not (pdf_keys & matched_pdf_names):
                pdf_affil = pdf_a.get("affiliation", "")
                merged.append({
                    "name": pdf_a["name"],
                    "affiliation": pdf_affil,
                    "email": pdf_a.get("email", ""),
                    "country": self._infer_country(pdf_affil),
                    "affiliation_source": "pdf_only",
                })

        return merged

    @staticmethod
    def _infer_country(affiliation: str) -> str:
        """Infer country from institution name using keyword matching."""
        if not affiliation:
            return ""
        aff = affiliation.lower()
        # Chinese institutions
        cn_kw = ["大学", "university of china", "chinese academy", "中国", "beijing",
                 "shanghai", "tsinghua", "peking", "zhejiang", "fudan", "nanjing",
                 "wuhan", "harbin", "beihang", "huazhong", "sjtu", "ustc",
                 "sun yat-sen", "southeast university", "tongji", "xidian",
                 "national university of defense", "中科院", "北京", "上海",
                 "深圳", "广州", "杭州", "南京", "武汉", "成都", "西安"]
        if any(k in aff for k in cn_kw):
            return "CN"
        # US institutions
        us_kw = ["mit", "stanford", "harvard", "berkeley", "carnegie mellon",
                 "princeton", "yale", "columbia university", "cornell", "ucla",
                 "university of california", "university of michigan",
                 "university of washington", "georgia tech", "uiuc",
                 "university of illinois", "notre dame", "michigan state",
                 "google", "openai", "meta ai", "microsoft research",
                 "nvidia", "apple", "amazon", "ibm research"]
        if any(k in aff for k in us_kw):
            return "US"
        # UK
        if any(k in aff for k in ["oxford", "cambridge", "imperial college",
                                   "university of london", "ucl", "edinburgh",
                                   "manchester", "leicester"]):
            return "GB"
        # Others
        _country_kw = {
            "CA": ["university of toronto", "mcgill", "waterloo", "montreal"],
            "DE": ["tu munich", "max planck", "heidelberg", "berlin"],
            "FR": ["inria", "grenoble", "paris", "sorbonne", "cnrs"],
            "JP": ["university of tokyo", "kyoto", "osaka", "riken"],
            "KR": ["kaist", "seoul national", "postech", "yonsei"],
            "SG": ["nus", "nanyang", "singapore"],
            "AU": ["university of sydney", "melbourne", "monash", "anu"],
            "CH": ["eth zurich", "epfl"],
            "IT": ["pavia", "politecnico di milano", "roma", "torino"],
            "IN": ["iit", "iisc", "indian institute"],
            "HK": ["hong kong"],
            "IL": ["technion", "hebrew university", "weizmann", "tel aviv"],
            "NL": ["delft", "amsterdam", "leiden", "eindhoven"],
        }
        for code, kws in _country_kw.items():
            if any(k in aff for k in kws):
                return code
        return ""

    @staticmethod
    def _name_keys(name: str) -> set:
        """Extract all name variants for matching (same logic as scholar dedup)."""
        keys = set()
        cleaned = name.strip()
        if not cleaned:
            return keys
        # Split on parentheses and slashes
        parts = re.split(r'[()（）/／]', cleaned)
        for part in parts:
            p = part.strip().strip(',，、').strip()
            if p and len(p) >= 2:
                keys.add(p.lower())
        base = re.sub(r'[（(].*?[）)]', '', cleaned).strip()
        if base and len(base) >= 2:
            keys.add(base.lower())
        return keys
