import json
from pathlib import Path

import pytest

from citationclaw.core.metadata_collector import MetadataCollector
from citationclaw.core.phase1_cache import Phase1Cache
from citationclaw.core.pipeline_adapter import PipelineAdapter


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cdp_cookie_dependencies_are_declared():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")

    for dep in ("websocket-client", "pycookiecheat"):
        assert dep in pyproject
        assert dep in requirements


@pytest.mark.asyncio
async def test_phase1_cache_canonicalizes_scholar_cites_urls(tmp_path):
    cache = Phase1Cache(cache_file=tmp_path / "phase1_cache.json")
    url_a = "https://scholar.google.com/scholar?cites=12345&hl=en&as_sdt=0,5"
    url_b = "https://scholar.google.com/scholar?hl=zh-CN&cites=12345&filter=0"

    paper = {
        "paper_link": "https://example.com/paper",
        "paper_title": "Cached Paper",
        "paper_year": 2024,
    }

    await cache.add_papers(url_a, {"paper_0": paper}, year=2024)
    await cache.mark_year_complete(url_a, 2024)

    assert cache.has_papers(url_b)
    assert cache.paper_count(url_b) == 1
    assert cache.cached_years(url_b)["2024"]["complete"] is True


def test_phase1_cache_migrates_legacy_url_keys(tmp_path):
    cache_file = tmp_path / "phase1_cache.json"
    url_a = "https://scholar.google.com/scholar?cites=777&hl=en"
    url_b = "https://scholar.google.com/scholar?cites=777&as_sdt=0,5"
    cache_file.write_text(
        json.dumps(
            {
                url_a: {
                    "url": url_a,
                    "complete": False,
                    "papers": {"a": {"paper_title": "A"}},
                    "years": {"2023": {"complete": True}},
                    "updated_at": "2026-01-01T00:00:00",
                },
                url_b: {
                    "url": url_b,
                    "complete": True,
                    "papers": {"b": {"paper_title": "B"}},
                    "years": {},
                    "updated_at": "2026-01-02T00:00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    cache = Phase1Cache(cache_file=cache_file)

    assert cache.is_complete(url_a)
    assert cache.paper_count(url_b) == 2
    assert cache.cached_years(url_b)["2023"]["complete"] is True


def test_metadata_collector_uses_unpaywall_pdf_when_openalex_has_none():
    collector = MetadataCollector()
    s2 = {
        "title": "Paper A",
        "year": 2020,
        "doi": "10.1234/Test",
        "arxiv_id": "",
        "cited_by_count": 1,
        "influential_citation_count": 0,
        "s2_id": "S2",
        "authors": [],
        "pdf_url": "",
        "venue": "",
    }

    result = collector._build_from_s2(
        s2,
        oa_supplement=None,
        unpaywall_pdf_url="https://repo.example/paper.pdf",
    )

    assert result["oa_pdf_url"] == "https://repo.example/paper.pdf"
    assert "unpaywall" in result["sources"]


def test_pipeline_adapter_exports_pdf_source_and_failure_summary():
    adapter = PipelineAdapter()
    paper = {
        "paper_title": "Merged Paper",
        "paper_link": "",
        "paper_year": 2025,
        "citation": "3",
        "authors_raw": {},
        "_pdf_source": "unpaywall",
        "_pdf_failures": [
            {"stage": "gs_link", "http_status": 403, "url": "https://blocked.example"},
            {"stage": "scraper_smart", "reason": "mojibake"},
        ],
    }

    record = adapter.to_legacy_record(
        paper=paper,
        metadata={"authors": [], "sources": [], "pdf_url": ""},
        self_citation={"is_self_citation": False, "method": "none"},
        renowned_scholars=[],
        citing_paper="Target",
        record_index=1,
        pdf_downloaded=False,
    )

    inner = record["1"]
    assert inner["PDF_Source"] == "unpaywall"
    assert "gs_link:http_status=403" in inner["PDF_Failure_Reasons"]
    assert "scraper_smart:reason=mojibake" in inner["PDF_Failure_Reasons"]


@pytest.mark.asyncio
async def test_url_finder_uses_persistent_title_cache(tmp_path, monkeypatch):
    from citationclaw.core import url_finder as uf

    cache_file = tmp_path / "url_finder_cache.json"
    cache_file.write_text(
        json.dumps(
            {
                "attention is all you need": (
                    "https://scholar.google.com/scholar?cites=123456789"
                )
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(uf, "_URL_CACHE_FILE", cache_file, raising=False)

    finder = uf.PaperURLFinder(api_keys=[], log_callback=lambda msg: None)

    assert await finder.find_citation_url("  Attention   Is All You Need  ") == (
        "https://scholar.google.com/scholar?cites=123456789"
    )
