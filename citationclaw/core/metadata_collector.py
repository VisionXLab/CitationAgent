"""S2-first metadata collector.

Primary: Semantic Scholar (like PaperRadar — one query returns everything).
Supplement: OpenAlex (h-index, OA PDF), Unpaywall (OA PDF by DOI),
arXiv (reliable PDF).

S2 gives: paperId, authors (with affiliations), DOI, ArXiv ID,
openAccessPdf, venue, year, citation count — all in one call.
"""
import asyncio
import re
from typing import Optional, List

from citationclaw.core.openalex_client import OpenAlexClient
from citationclaw.core.s2_client import S2Client
from citationclaw.core.arxiv_client import ArxivClient
from citationclaw.core.unpaywall_client import UnpaywallClient


def _normalize_doi(doi: str) -> str:
    """Strip 'https://doi.org/' prefix and lowercase — downstream cache/dedup
    must use a canonical form regardless of whether S2 or OpenAlex was the
    primary source (S2 returns '10.x/...' while OpenAlex returns the URL form).
    """
    if not doi:
        return ""
    d = doi.strip()
    for p in ("https://doi.org/", "http://doi.org/",
              "https://dx.doi.org/", "http://dx.doi.org/"):
        if d.lower().startswith(p):
            d = d[len(p):]
            break
    return d.lower()


async def _async_none():
    """Awaitable placeholder for optional gather slots."""
    return None


class MetadataCollector:
    def __init__(self, email: Optional[str] = None, s2_api_key: Optional[str] = None):
        self.openalex = OpenAlexClient(email=email)
        self.s2 = S2Client(api_key=s2_api_key)
        self.arxiv = ArxivClient()
        self.unpaywall = UnpaywallClient(email=email or "citationclaw@research.tool")
        self._has_s2_key = bool(s2_api_key)

    async def collect(self, title: str, paper_url: str = "") -> Optional[dict]:
        """S2-first metadata collection.

        Strategy:
        1. Query S2 by title (primary)
        2. If S2 title miss + have URL → try S2 by URL (paper_link from GS)
        3. If S2 found → supplement with OpenAlex
        4. If S2 missed → fallback to OpenAlex + arXiv parallel query
        """
        # Step 1: S2 search by title
        s2_result = None
        try:
            s2_result = await self.s2.search_paper(title)
        except Exception:
            pass

        # Step 2: S2 title miss → try by URL (like PaperRadar uses paperId)
        if not s2_result and paper_url and "scholar.google" not in paper_url:
            try:
                s2_result = await self.s2.search_by_url(paper_url)
            except Exception:
                pass

        if s2_result:
            # S2 found: supplement from OpenAlex and DOI-based Unpaywall in parallel.
            s2_doi = _normalize_doi(s2_result.get("doi", ""))
            oa_task = self.openalex.search_work(title)
            up_task = self.unpaywall.lookup(s2_doi) if s2_doi else _async_none()
            oa_result, up_pdf = await asyncio.gather(
                oa_task, up_task, return_exceptions=True
            )
            if isinstance(oa_result, Exception):
                oa_result = None
            if isinstance(up_pdf, Exception):
                up_pdf = None
            return self._build_from_s2(
                s2_result,
                oa_supplement=oa_result,
                unpaywall_pdf_url=up_pdf,
            )

        # Step 3: S2 missed entirely — parallel fallback to OpenAlex + arXiv
        oa_result, arxiv_result = await asyncio.gather(
            self.openalex.search_work(title),
            self.arxiv.search_paper(title),
            return_exceptions=True,
        )
        if isinstance(oa_result, Exception):
            oa_result = None
        if isinstance(arxiv_result, Exception):
            arxiv_result = None

        if oa_result or arxiv_result:
            up_pdf = None
            doi = _normalize_doi((oa_result or {}).get("doi", "")) if oa_result else ""
            if doi:
                try:
                    up_pdf = await self.unpaywall.lookup(doi)
                except Exception:
                    up_pdf = None
            return self._build_from_fallback(
                oa_result,
                arxiv_result,
                unpaywall_pdf_url=up_pdf,
            )

        return None

    def _build_from_s2(
        self,
        s2: dict,
        oa_supplement: Optional[dict] = None,
        unpaywall_pdf_url: Optional[str] = None,
    ) -> dict:
        """Build result with S2 as primary (PaperRadar-style)."""
        # S2 _parse_paper already extracts arxiv_id, doi, and builds pdf_url fallback chain
        arxiv_id = s2.get("arxiv_id", "")
        s2_doi = _normalize_doi(s2.get("doi", ""))
        pdf_url = s2.get("pdf_url", "")

        result = {
            "title": s2.get("title", ""),
            "year": s2.get("year"),
            "doi": s2_doi,
            "cited_by_count": s2.get("cited_by_count", 0),
            "influential_citation_count": s2.get("influential_citation_count", 0),
            "s2_id": s2.get("s2_id", ""),
            "arxiv_id": arxiv_id,
            "venue": s2.get("venue", ""),
            "pdf_url": pdf_url,
            "oa_pdf_url": "",
            "authors": s2.get("authors", []),
            "sources": ["s2"],
        }

        # Supplement from OpenAlex if available
        if oa_supplement:
            result["sources"].append("openalex")
            result["openalex_id"] = oa_supplement.get("openalex_id", "")
            result["oa_pdf_url"] = oa_supplement.get("oa_pdf_url", "")

            # If S2 has no venue, use OpenAlex
            if not result["venue"]:
                result["venue"] = oa_supplement.get("venue", "")

            # Enrich S2 authors with OpenAlex openalex_id + affiliation
            oa_authors = oa_supplement.get("authors", [])
            if oa_authors:
                self._enrich_s2_authors(result["authors"], oa_authors)

        if not result["oa_pdf_url"] and unpaywall_pdf_url:
            result["oa_pdf_url"] = unpaywall_pdf_url
            result["sources"].append("unpaywall")

        return result

    def _build_from_fallback(
        self,
        oa: Optional[dict],
        arxiv: Optional[dict],
        unpaywall_pdf_url: Optional[str] = None,
    ) -> dict:
        """Build result from OpenAlex/arXiv when S2 missed."""
        primary = oa or arxiv
        result = {
            "title": primary.get("title", ""),
            "year": primary.get("year"),
            "doi": _normalize_doi(primary.get("doi", "")),
            "cited_by_count": primary.get("cited_by_count", 0),
            "influential_citation_count": 0,
            "s2_id": "",
            "venue": primary.get("venue", ""),
            "sources": [],
        }

        if oa:
            result["sources"].append("openalex")
            result["openalex_id"] = oa.get("openalex_id", "")
            result["oa_pdf_url"] = oa.get("oa_pdf_url", "")
            result["authors"] = oa.get("authors", [])
        else:
            result["oa_pdf_url"] = ""
            result["authors"] = []

        if arxiv:
            result["sources"].append("arxiv")
            result["arxiv_id"] = arxiv.get("arxiv_id", "")
            if not result["authors"]:
                result["authors"] = arxiv.get("authors", [])
        else:
            result["arxiv_id"] = ""

        # PDF URL
        pdf_url = ""
        if arxiv and arxiv.get("pdf_url"):
            pdf_url = arxiv["pdf_url"]
        elif oa and oa.get("pdf_url"):
            pdf_url = oa["pdf_url"]
        result["pdf_url"] = pdf_url

        # Extract arxiv_id from pdf_url if not set
        if not result.get("arxiv_id") and pdf_url and "arxiv.org" in pdf_url:
            m = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+)', pdf_url)
            if m:
                result["arxiv_id"] = m.group(1)

        if not result["oa_pdf_url"] and unpaywall_pdf_url:
            result["oa_pdf_url"] = unpaywall_pdf_url
            result["sources"].append("unpaywall")

        return result

    @staticmethod
    def _enrich_s2_authors(s2_authors: list, oa_authors: list):
        """Enrich S2 author list with OpenAlex data (openalex_id, affiliation if missing)."""
        if not oa_authors:
            return
        oa_by_name = {}
        for a in oa_authors:
            name = a.get("name", "").strip().lower()
            if name:
                oa_by_name[name] = a

        for a in s2_authors:
            name_lower = a.get("name", "").strip().lower()
            oa_match = oa_by_name.get(name_lower)
            if not oa_match:
                # Try last name match
                parts = name_lower.split()
                if parts:
                    for oa_name, oa_a in oa_by_name.items():
                        if oa_name.split()[-1] == parts[-1]:
                            oa_match = oa_a
                            break
            if oa_match:
                if not a.get("openalex_id") and oa_match.get("openalex_id"):
                    a["openalex_id"] = oa_match["openalex_id"]
                if not a.get("affiliation") and oa_match.get("affiliation"):
                    a["affiliation"] = oa_match["affiliation"]
                if not a.get("country") and oa_match.get("country"):
                    a["country"] = oa_match["country"]

    async def batch_collect(self, titles: List[str], concurrency: int = 10) -> List[Optional[dict]]:
        """Collect metadata for multiple papers concurrently."""
        sem = asyncio.Semaphore(concurrency)

        async def _collect(t):
            async with sem:
                return await self.collect(t)

        return await asyncio.gather(*[_collect(t) for t in titles])

    async def close(self):
        await self.openalex.close()
        await self.s2.close()
        await self.arxiv.close()
        await self.unpaywall.close()
