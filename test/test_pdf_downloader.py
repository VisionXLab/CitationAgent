"""Tests for PDF downloader."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from citationclaw.core.pdf_downloader import (
    PDFDownloader, _transform_url, _extract_pdf_url_from_html, _build_cvf_candidates,
    _detect_publisher, _publisher_from_doi, _SCRAPER_PUBLISHER_PROFILES,
    _pdf_title_matches,
)


def test_cache_path(tmp_path):
    dl = PDFDownloader(cache_dir=tmp_path)
    paper = {"doi": "10.1234/test"}
    path = dl._cache_path(paper)
    assert path.parent == tmp_path
    assert path.suffix == ".pdf"


def test_cache_path_title_fallback(tmp_path):
    dl = PDFDownloader(cache_dir=tmp_path)
    paper = {"title": "My Paper Title"}
    path = dl._cache_path(paper)
    assert path.suffix == ".pdf"


def test_transform_url_cvf():
    url = "https://openaccess.thecvf.com/content/CVPR2025/html/Author_Title_CVPR_2025_paper.html"
    result = _transform_url(url)
    assert "/papers/" in result
    assert result.endswith("_paper.pdf")


def test_transform_url_openreview():
    url = "https://openreview.net/forum?id=abc123"
    assert _transform_url(url) == "https://openreview.net/pdf?id=abc123"


def test_transform_url_arxiv():
    url = "https://arxiv.org/abs/2505.12345"
    assert _transform_url(url) == "https://arxiv.org/pdf/2505.12345"


def test_transform_url_ieee():
    url = "https://ieeexplore.ieee.org/abstract/document/10804848/"
    result = _transform_url(url)
    assert "stamp.jsp" in result
    assert "10804848" in result


def test_transform_url_springer():
    url = "https://link.springer.com/article/10.1007/s12345-025-00001-2"
    result = _transform_url(url)
    assert "/content/pdf/" in result
    assert result.endswith(".pdf")


def test_transform_url_mdpi():
    url = "https://www.mdpi.com/1424-8220/25/1/65"
    assert _transform_url(url).endswith("/pdf")


def test_transform_url_sciencedirect():
    url = "https://www.sciencedirect.com/science/article/pii/S1566253525001234"
    assert "/pdfft" in _transform_url(url)


def test_transform_url_acl():
    url = "https://aclanthology.org/2024.acl-main.123"
    assert _transform_url(url).endswith(".pdf")


def test_extract_pdf_from_ieee_html():
    html = '<script>var xplGlobal={"pdfUrl":"/stamp/stamp.jsp?tp=&arnumber=123"}</script>'
    result = _extract_pdf_url_from_html(html, "https://ieeexplore.ieee.org/document/123")
    assert result is not None
    assert "stamp" in result


def test_extract_pdf_from_meta_tag():
    html = '<meta name="citation_pdf_url" content="https://example.com/paper.pdf">'
    result = _extract_pdf_url_from_html(html, "https://example.com")
    assert result == "https://example.com/paper.pdf"


def test_build_cvf_candidates():
    urls = _build_cvf_candidates("10.1109/cvpr.2025.123", "CVPR", 2025, "My Paper Title", "Smith")
    assert len(urls) >= 1
    assert "openaccess.thecvf.com" in urls[0]
    assert "CVPR2025" in urls[0]


def test_build_cvf_no_match():
    urls = _build_cvf_candidates("10.1234/other", "ICML", 2025, "Title", "Author")
    assert len(urls) == 0  # ICML is not CVF


# ── Publisher detection tests ────────────────────────────────────────────

class TestDetectPublisher:
    def test_ieee_url(self):
        assert _detect_publisher("https://ieeexplore.ieee.org/document/10804848/") == "ieee"

    def test_springer_url(self):
        assert _detect_publisher("https://link.springer.com/article/10.1007/s12345") == "springer"

    def test_elsevier_url(self):
        assert _detect_publisher("https://www.sciencedirect.com/science/article/pii/S1234") == "elsevier"

    def test_acm_url(self):
        assert _detect_publisher("https://dl.acm.org/doi/10.1145/12345") == "acm"

    def test_wiley_url(self):
        assert _detect_publisher("https://onlinelibrary.wiley.com/doi/10.1002/abc") == "wiley"

    def test_unknown_url(self):
        assert _detect_publisher("https://arxiv.org/abs/2505.12345") == "unknown"

    def test_empty_url(self):
        assert _detect_publisher("") == "unknown"


class TestPublisherFromDoi:
    def test_ieee_doi(self):
        assert _publisher_from_doi("10.1109/TPAMI.2024.3409904") == "ieee"

    def test_springer_doi(self):
        assert _publisher_from_doi("10.1007/s11263-024-02006-w") == "springer"

    def test_elsevier_doi(self):
        assert _publisher_from_doi("10.1016/j.patcog.2024.110345") == "elsevier"

    def test_acm_doi(self):
        assert _publisher_from_doi("10.1145/3597503.3639187") == "acm"

    def test_wiley_doi(self):
        assert _publisher_from_doi("10.1002/adma.202400123") == "wiley"

    def test_unknown_doi(self):
        assert _publisher_from_doi("10.48550/arXiv.2505.12345") == "unknown"

    def test_empty_doi(self):
        assert _publisher_from_doi("") == "unknown"


class TestPublisherProfiles:
    def test_ieee_uses_ultra_premium(self):
        profile = _SCRAPER_PUBLISHER_PROFILES["ieee"]
        assert profile.get("ultra_premium") == "true"
        assert profile.get("render") == "true"

    def test_springer_uses_premium(self):
        profile = _SCRAPER_PUBLISHER_PROFILES["springer"]
        assert profile.get("premium") == "true"
        assert profile.get("render") == "true"
        assert "ultra_premium" not in profile

    def test_elsevier_uses_premium(self):
        profile = _SCRAPER_PUBLISHER_PROFILES["elsevier"]
        assert profile.get("premium") == "true"
        assert profile.get("country_code") == "us"

    def test_default_profile_exists(self):
        assert "_default" in _SCRAPER_PUBLISHER_PROFILES


class TestScraperBuildUrl:
    def test_build_ieee_url(self, tmp_path):
        dl = PDFDownloader(cache_dir=tmp_path, scraper_api_keys=["test_key_123"])
        url = dl._scraper_build_url(
            "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=123",
            "ieee", session_number=999999
        )
        assert url is not None
        assert "api_key=test_key_123" in url
        assert "ultra_premium=true" in url
        assert "render=true" in url
        assert "session_number=999999" in url

    def test_build_springer_url(self, tmp_path):
        dl = PDFDownloader(cache_dir=tmp_path, scraper_api_keys=["key1"])
        url = dl._scraper_build_url(
            "https://link.springer.com/content/pdf/10.1007/s12345.pdf",
            "springer"
        )
        assert "premium=true" in url
        assert "ultra_premium" not in url

    def test_no_keys_returns_none(self, tmp_path):
        dl = PDFDownloader(cache_dir=tmp_path, scraper_api_keys=[])
        url = dl._scraper_build_url("https://example.com", "ieee")
        assert url is None


class TestPdfTitleMatches:
    """Test the PDF title verification guard."""

    def _make_fake_pdf(self, first_page_text: str) -> bytes:
        """Create a minimal PDF with given first-page text using PyMuPDF."""
        try:
            import fitz
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), first_page_text)
            data = doc.tobytes()
            doc.close()
            return data
        except ImportError:
            pytest.skip("PyMuPDF not installed")

    def test_matching_title(self):
        pdf = self._make_fake_pdf("Attention Is All You Need\nVaswani et al.")
        assert _pdf_title_matches(pdf, "Attention Is All You Need") is True

    def test_mismatched_title(self):
        # Text must be >50 chars for verification to trigger
        pdf = self._make_fake_pdf(
            "Radiation Resistant Camera System for Monitoring Deuterium "
            "Plasma Discharges in the Large Helical Device with Advanced Sensors"
        )
        assert _pdf_title_matches(pdf, "Attention Is All You Need") is False

    def test_overlapping_field_rejected(self):
        """Papers in same field (underwater detection) sharing keywords should be rejected."""
        pdf = self._make_fake_pdf(
            "SPMamba-YOLO: An Underwater Object Detection Network Based on "
            "Multi-Scale Feature Enhancement and Global Context Modeling "
            "with Advanced Information Processing Pipeline"
        )
        # Shares 6 words but missing key identifiers like USOD, multimodal, salient, fusion
        assert _pdf_title_matches(pdf,
            "IF-USOD: Multimodal information fusion interactive feature "
            "enhancement architecture for underwater salient object detection"
        ) is False

    def test_acronym_must_match(self):
        """Distinctive acronyms like BERT, USOD must appear on first page."""
        pdf = self._make_fake_pdf(
            "A study on pre-training deep bidirectional transformers "
            "for language understanding with modern architectures and methods"
        )
        # Has overlapping words but missing "BERT" acronym
        assert _pdf_title_matches(pdf,
            "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
        ) is False

    def test_acronym_present_passes(self):
        """When acronym is present, paper should pass."""
        pdf = self._make_fake_pdf(
            "BERT: Pre-training of Deep Bidirectional Transformers "
            "for Language Understanding by Devlin et al from Google Research"
        )
        assert _pdf_title_matches(pdf,
            "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
        ) is True

    def test_partial_match_accepted(self):
        pdf = self._make_fake_pdf("Attention Mechanisms Are All You Need for NLP Tasks")
        assert _pdf_title_matches(pdf, "Attention Is All You Need") is True

    def test_short_title_skipped(self):
        pdf = self._make_fake_pdf("Some random content")
        assert _pdf_title_matches(pdf, "BERT") is True  # Too short to verify

    def test_empty_title_skipped(self):
        assert _pdf_title_matches(b"%PDF-fake", "") is True

    def test_invalid_pdf_accepted(self):
        assert _pdf_title_matches(b"not a pdf", "Some Title Here") is True

    def test_pymupdf_missing_accepted(self):
        """If fitz import fails, should accept (not block)."""
        # _pdf_title_matches catches ImportError → returns True
        assert _pdf_title_matches(b"%PDF-1.4 broken", "Any Title") is True


class TestExtractIeeePdf:
    def test_pdf_url_json(self):
        html = '<script>var meta = {"pdfUrl": "/stamp/stamp.jsp?tp=&arnumber=123"};</script>'
        result = PDFDownloader._extract_ieee_pdf(html, "https://ieeexplore.ieee.org/document/123")
        assert result is not None
        assert "stamp" in result

    def test_iframe_get_pdf(self):
        html = '<iframe src="https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=456"></iframe>'
        result = PDFDownloader._extract_ieee_pdf(html, "https://ieeexplore.ieee.org/document/456")
        assert result is not None
        assert "getPDF" in result

    def test_arnumber_fallback(self):
        html = '<script>xplGlobal = {"document": {"arnumber": "789"}};</script>'
        result = PDFDownloader._extract_ieee_pdf(html, "https://ieeexplore.ieee.org/document/789")
        assert result is not None
        assert "789" in result

    def test_iel7_direct_link(self):
        html = '<script>var pdfUrl = "https://ieeexplore.ieee.org/iel7/6287639/123/pdf.pdf";</script>'
        result = PDFDownloader._extract_ieee_pdf(html, "https://ieeexplore.ieee.org/document/123")
        assert result is not None
        assert "iel7" in result


class TestExtractElsevierPdf:
    def test_pdf_link_json(self):
        html = '<script>window.__NEXT_DATA__ = {"pdfLink": "/science/article/pii/S123/pdfft"};</script>'
        result = PDFDownloader._extract_elsevier_pdf(html, "https://www.sciencedirect.com/article")
        assert result is not None
        assert "pdfft" in result

    def test_citation_meta(self):
        html = '<meta name="citation_pdf_url" content="https://pdf.sciencedirectassets.com/123/paper.pdf">'
        result = PDFDownloader._extract_elsevier_pdf(html, "https://www.sciencedirect.com/article")
        assert result == "https://pdf.sciencedirectassets.com/123/paper.pdf"

    def test_pii_construction(self):
        url = "https://www.sciencedirect.com/science/article/pii/S0031320324001234"
        result = PDFDownloader._extract_elsevier_pdf("<html></html>", url)
        assert result is not None
        assert "S0031320324001234" in result
        assert "pdfft" in result


class TestExtractSpringerPdf:
    def test_citation_meta(self):
        html = '<meta name="citation_pdf_url" content="https://link.springer.com/content/pdf/10.1007/s123.pdf">'
        result = PDFDownloader._extract_springer_pdf(html, "https://link.springer.com/article/10.1007/s123")
        assert result is not None
        assert "content/pdf" in result

    def test_doi_fallback(self):
        result = PDFDownloader._extract_springer_pdf("<html></html>", "https://link.springer.com/article", "10.1007/s123-025-00001-2")
        assert result == "https://link.springer.com/content/pdf/10.1007/s123-025-00001-2.pdf"

    def test_direct_pdf_link(self):
        html = '<a href="https://link.springer.com/content/pdf/10.1007/s999.pdf">Download PDF</a>'
        result = PDFDownloader._extract_springer_pdf(html, "https://link.springer.com/article")
        assert result == "https://link.springer.com/content/pdf/10.1007/s999.pdf"
