"""Tests for PDF downloader."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from citationclaw.core.pdf_downloader import (
    PDFDownloader, _transform_url, _extract_pdf_url_from_html, _build_cvf_candidates,
    _detect_publisher, _publisher_from_doi, _SCRAPER_PUBLISHER_PROFILES,
    _pdf_title_matches, _pdf_bytes_are_mojibake, DEFAULT_CACHE_DIR,
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
        # 2026-04-21: 10.48550 is now recognized as arxiv (see
        # TestArxivDoiRecognition below). Use a truly unknown prefix.
        assert _publisher_from_doi("10.99999/unknown.example.2025") == "unknown"

    def test_empty_doi(self):
        assert _publisher_from_doi("") == "unknown"


class TestPublisherProfiles:
    # NOTE (2026-04-20): the deployed ScraperAPI key is on the standard
    # 100k-credit plan which does NOT support `ultra_premium`. Sending that
    # flag makes ScraperAPI return HTTP 500 (observed in the harness run).
    # All publisher profiles are therefore on `premium=true`. When the
    # account upgrades, IEEE / Wiley / Elsevier should re-enable ultra_premium
    # and these tests should be tightened accordingly.
    def test_ieee_uses_premium(self):
        profile = _SCRAPER_PUBLISHER_PROFILES["ieee"]
        assert profile.get("premium") == "true"
        assert profile.get("render") == "true"
        # Standard plan incompatibility — explicit no-ultra_premium guard.
        assert "ultra_premium" not in profile

    def test_springer_uses_premium(self):
        profile = _SCRAPER_PUBLISHER_PROFILES["springer"]
        assert profile.get("premium") == "true"
        assert profile.get("render") == "true"
        assert "ultra_premium" not in profile

    def test_elsevier_uses_premium(self):
        profile = _SCRAPER_PUBLISHER_PROFILES["elsevier"]
        assert profile.get("premium") == "true"
        assert profile.get("country_code") == "us"
        assert "ultra_premium" not in profile

    def test_wiley_uses_premium(self):
        profile = _SCRAPER_PUBLISHER_PROFILES["wiley"]
        assert profile.get("premium") == "true"
        assert profile.get("render") == "true"
        assert "ultra_premium" not in profile

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
        # Downgraded from ultra_premium -> premium for standard-plan compatibility.
        assert "premium=true" in url
        assert "ultra_premium=true" not in url
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


# ── Phase-2 bug fixes (2026-04-19): absolute cache dir + mojibake detection ──

class TestAbsoluteCacheDir:
    """Bug #2: cache dir must be absolute so it stays stable across CWD changes."""

    def test_default_cache_dir_is_absolute(self):
        assert DEFAULT_CACHE_DIR.is_absolute(), (
            f"DEFAULT_CACHE_DIR must be absolute (was relative: {DEFAULT_CACHE_DIR}); "
            f"otherwise harness/CWD-switches fragment the cache"
        )

    def test_cache_path_is_absolute(self, tmp_path):
        dl = PDFDownloader()  # uses DEFAULT_CACHE_DIR (absolute)
        p = dl._cache_path({"doi": "10.1234/x"})
        assert p.is_absolute()

    def test_normalize_doi_stable_cache_key(self, tmp_path):
        """Same paper with different DOI casings/prefixes → same cache hash."""
        dl = PDFDownloader(cache_dir=tmp_path)
        a = dl._cache_path({"doi": "10.1109/ABC.2024.123"})
        b = dl._cache_path({"doi": "https://doi.org/10.1109/ABC.2024.123"})
        c = dl._cache_path({"doi": "10.1109/abc.2024.123"})
        assert a == b == c


class TestMojibakeDetection:
    """Bug #1: _pdf_bytes_are_mojibake catches both corruption flavors."""

    def test_clean_pdf_header_accepted(self):
        # Real PDF: 4 raw high-bit bytes after the version line
        data = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n" + b"x" * 2000
        assert _pdf_bytes_are_mojibake(data) is False

    def test_hard_mojibake_ufffd_detected(self):
        # `response.text` turned 4 high-bit bytes into 4 U+FFFD
        data = b"%PDF-1.7\n%" + b"\xef\xbf\xbd" * 4 + b"\n1 0 obj\n" + b"x" * 2000
        assert _pdf_bytes_are_mojibake(data) is True

    def test_soft_mojibake_c3_doubling_detected(self):
        # Latin-1 decode + UTF-8 re-encode: each 0x80+ byte becomes \xc3\xXX
        # Original 4 bytes \xe2\xe3\xcf\xd3 -> 8 bytes with \xc3 every other
        data = (b"%PDF-1.5\n%\xc3\xa2\xc3\xa3\xc3\x8f\xc3\x93\n"
                + b"1 0 obj\n" + b"x" * 2000)
        assert _pdf_bytes_are_mojibake(data) is True

    def test_cache_is_valid_rejects_mojibake(self, tmp_path):
        dl = PDFDownloader(cache_dir=tmp_path)
        p = tmp_path / "bad.pdf"
        p.write_bytes(b"%PDF-1.7\n%" + b"\xef\xbf\xbd" * 4 + b"\n" + b"x" * 2000)
        assert dl._cache_is_valid(p, "Some Paper Title") is False
        # Also without a title — the mojibake guard must still fire
        assert dl._cache_is_valid(p, "") is False

    def test_cache_is_valid_accepts_clean_without_title(self, tmp_path):
        dl = PDFDownloader(cache_dir=tmp_path)
        p = tmp_path / "ok.pdf"
        p.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"x" * 2000)
        assert dl._cache_is_valid(p, "") is True


class TestScraperApiMojibakeIntegration:
    """2026-04-20 regression: ScraperAPI smart download path used to bypass
    the mojibake guard because cascade step 15 wrote bytes with only a raw
    `%PDF-` check. Verify those gates are now in place."""

    def test_scraper_smart_label_registered(self):
        # Cascade step 15 now logs via `_ok(data, "scraper_smart")` which
        # routes through _SOURCE_LABELS. Confirm the label exists so logs
        # don't show the raw key.
        from citationclaw.core.pdf_downloader import _SOURCE_LABELS
        assert _SOURCE_LABELS.get("scraper_smart") == "ScraperAPI智能下载"

    def test_smart_scraper_url_picks_raw_fetch_for_pdf_urls(self, tmp_path):
        # The docstring contract: URLs ending in .pdf / containing /pdf/
        # skip render=true on the first hop to avoid the binary-through-
        # headless-browser mojibake path.
        dl = PDFDownloader(cache_dir=tmp_path, scraper_api_keys=["k"])
        import inspect
        src = inspect.getsource(dl._smart_scraper_download)
        assert 'pdf_like' in src
        assert 'endswith(".pdf")' in src
        assert '_pdf_bytes_are_mojibake' in src

    def test_no_publisher_profile_uses_ultra_premium(self):
        # Standard-plan compatibility: every publisher profile must stay
        # off ultra_premium (otherwise ScraperAPI 500s). This catches any
        # future accidental re-introduction.
        from citationclaw.core.pdf_downloader import _SCRAPER_PUBLISHER_PROFILES
        for name, profile in _SCRAPER_PUBLISHER_PROFILES.items():
            assert "ultra_premium" not in profile, (
                f"profile {name!r} must not use ultra_premium on the standard plan"
            )


class TestVApiIntegration:
    """2026-04-20: V-API (gpt.ge / gemini-3-flash-preview-search) activation.

    `enable_pdf_llm_search` was False by default; the 2026-04-20 harness
    showed "LLM搜索: 禁用" and 4 papers failed with no free-alternative
    fallback. config.json now flips it on, and `_llm_search_alternative_pdf`
    has a ScraperAPI rescue for candidate URLs that block direct fetches.
    """

    def test_config_json_has_llm_search_enabled(self):
        # The deployed config in this repo must keep LLM search on so the
        # harness runs (which read this config) actually exercise V-API.
        # 2026-04-20 regression: the flag was silently wiped because the
        # UI's `ConfigUpdate` schema (`app/main.py`) didn't include it, so
        # any UI save defaulted the field back to False on disk. The fix
        # is in three places: (1) added to ConfigUpdate; (2) added to the
        # sensitive-key preservation list; (3) ConfigManager.get() auto-
        # reloads on disk mtime change so manual re-flips take effect.
        # This test locks in the on-disk value; the ConfigUpdate schema
        # guard is in `TestConfigUpdateSchema` below.
        import json
        import pathlib
        cfg = json.loads(
            pathlib.Path(__file__).resolve().parent.parent
            .joinpath("config.json").read_text(encoding="utf-8")
        )
        assert cfg.get("enable_pdf_llm_search") is True, (
            "config.json must keep LLM PDF search enabled — 2026-04-20 harness "
            "showed this is required for free-alternative recovery"
        )

    def test_config_update_schema_has_llm_search_field(self):
        # Silent-wipe prevention: `enable_pdf_llm_search` must appear in
        # the POST `/api/config` schema so Pydantic doesn't drop it
        # during any UI round-trip save.
        from citationclaw.app.main import ConfigUpdate
        assert "enable_pdf_llm_search" in ConfigUpdate.model_fields, (
            "ConfigUpdate must carry enable_pdf_llm_search to survive UI saves"
        )

    def test_config_update_schema_preserves_all_app_fields(self):
        # Future-proof: every AppConfig field except `enable_year_traverse`
        # (explicitly stripped at save time) must be representable in the
        # UI save schema, otherwise it gets silently reset on round-trip.
        from citationclaw.app.config_manager import AppConfig
        from citationclaw.app.main import ConfigUpdate
        app_fields = set(AppConfig.model_fields.keys())
        upd_fields = set(ConfigUpdate.model_fields.keys())
        # enable_year_traverse is intentionally excluded from round-trip
        # saves (reset to False every startup).
        missing = app_fields - upd_fields - {"enable_year_traverse"}
        assert not missing, (
            f"ConfigUpdate is missing AppConfig fields (will be silent-wiped "
            f"on any UI save): {sorted(missing)}"
        )

    def test_config_manager_auto_reloads_on_disk_change(self, tmp_path):
        # ConfigManager.get() must notice when config.json has been
        # modified on disk since the last load, so manual edits take
        # effect without a server restart.
        from citationclaw.app.config_manager import ConfigManager
        import json, time
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"enable_pdf_llm_search": False}),
                     encoding="utf-8")
        cm = ConfigManager(config_path=str(p))
        assert cm.get().enable_pdf_llm_search is False
        # Bump mtime at least 1 second (coarse FS mtime resolution on win)
        time.sleep(1.1)
        p.write_text(json.dumps({"enable_pdf_llm_search": True}),
                     encoding="utf-8")
        assert cm.get().enable_pdf_llm_search is True, (
            "ConfigManager.get() must reload when config.json mtime advances"
        )

    def test_app_config_strips_leading_space_in_api_key(self):
        # 2026-04-20: the deployed config.json had
        #   "openai_api_key": " sk-o37..."
        # (leading space from a copy-paste out of the V-API console).
        # Every LLM call 401'd with 无效的令牌 and the circuit breaker
        # disabled LLM search for the whole run. Pydantic field_validator
        # added to AppConfig must silently strip whitespace on all known
        # key/token fields.
        from citationclaw.app.config_manager import AppConfig
        c = AppConfig(openai_api_key="  sk-abc123\n")
        assert c.openai_api_key == "sk-abc123", (
            "openai_api_key must be whitespace-stripped on construction"
        )

    def test_app_config_strips_all_sensitive_fields(self):
        # Belt-and-suspenders: validator must apply to every entry in
        # _SENSITIVE_STRIP_FIELDS. Whitespace-only input becomes empty
        # string (consistent with the "no credential provided" default).
        from citationclaw.app.config_manager import (
            AppConfig, _SENSITIVE_STRIP_FIELDS,
        )
        data = {f: f"  value-for-{f}  " for f in _SENSITIVE_STRIP_FIELDS}
        c = AppConfig(**data)
        for f in _SENSITIVE_STRIP_FIELDS:
            assert getattr(c, f) == f"value-for-{f}", (
                f"{f!r} must be whitespace-stripped; got "
                f"{getattr(c, f)!r}"
            )
        # Empty after strip is fine (same as default).
        c = AppConfig(openai_api_key="   \t\n")
        assert c.openai_api_key == ""

    def test_config_manager_strips_disk_json_whitespace(self, tmp_path):
        # Simulate the exact bug: JSON on disk has a leading space in
        # openai_api_key. ConfigManager.get() must return a trimmed
        # value so downstream clients (OpenAI SDK, V-API retry loop)
        # don't 401.
        from citationclaw.app.config_manager import ConfigManager
        import json
        p = tmp_path / "config.json"
        p.write_text(json.dumps({
            "openai_api_key": " sk-leading-space",
            "s2_api_key": "s2-key\n",
            "mineru_api_token": " mineru-token ",
        }), encoding="utf-8")
        cfg = ConfigManager(config_path=str(p)).get()
        assert cfg.openai_api_key == "sk-leading-space"
        assert cfg.s2_api_key == "s2-key"
        assert cfg.mineru_api_token == "mineru-token"

    def test_scraper_fetch_url_helper_exists(self, tmp_path):
        dl = PDFDownloader(cache_dir=tmp_path, scraper_api_keys=["k"])
        assert hasattr(dl, "_scraper_fetch_url")

    def test_scraper_fetch_url_no_keys_returns_none(self, tmp_path):
        import asyncio
        dl = PDFDownloader(cache_dir=tmp_path, scraper_api_keys=[])

        async def run():
            return await dl._scraper_fetch_url("https://example.com/paper.pdf")
        assert asyncio.get_event_loop().run_until_complete(run()) is None

    def test_llm_search_calls_scraper_rescue(self, tmp_path):
        # Source-level guarantee that the V-API candidate loop falls back
        # to `_scraper_fetch_url` on direct-fetch failures.
        dl = PDFDownloader(cache_dir=tmp_path)
        import inspect
        src = inspect.getsource(dl._llm_search_alternative_pdf)
        assert "_scraper_fetch_url" in src, (
            "V-API search should rescue via ScraperAPI when the direct fetch "
            "of a candidate URL fails"
        )

    def test_llm_search_own_retry_loop_and_no_sdk_retry(self, tmp_path):
        # Live probe (2026-04-20) showed V-API's gpt.ge answers 429
        # "upstream_error 负载已饱和" on the first attempt for most
        # search-grounded queries. The function must therefore:
        #   (a) disable OpenAI SDK's default max_retries=2 (which would
        #       compound into 270s waits against a 90s hang),
        #   (b) own a retry loop that respects 429 but fails fast on
        #       401 / 403 / timeout,
        #   (c) NOT auto-disable the whole run on a single 429 — use a
        #       circuit-breaker counter instead.
        dl = PDFDownloader(cache_dir=tmp_path)
        import inspect
        src = inspect.getsource(dl._llm_search_alternative_pdf)
        assert "max_retries=0" in src, (
            "OpenAI SDK's default 2 retries must be disabled — they compound "
            "against 90s upstream hangs"
        )
        assert "5s 后重试" in src or "s 后重试" in src, (
            "own 429-retry loop must back off before retrying"
        )
        assert "_llm_search_429_misses" in src, (
            "persistent-429 circuit breaker must use a counter field"
        )


class TestCdpHelpers:
    """2026-04-20: regression guards for the CDP helper path.

    Historical silent-failure: `pdf_downloader.py` was missing a top-level
    `import json`, so every CDP helper that called `json.loads(...)` raised
    NameError, got swallowed by the blanket `except Exception: return False`,
    and CDP appeared to be "never connected" even when the debug browser
    was healthy on the port. Surfaced when the Phase 2 login checkpoint
    (added 2026-04-20) tried to auto-launch Chrome and logged
    `无法启动调试浏览器（port=9222）` even though port 9222 was listening.
    """

    def test_pdf_downloader_imports_json_at_module_level(self):
        # _cdp_check_connection / _cdp_list_tabs / _cdp_open_page /
        # _cdp_call all reference bare `json` from module globals. If the
        # import is dropped, every CDP call falls back to False silently.
        from citationclaw.core import pdf_downloader as pdl
        assert hasattr(pdl, "json"), (
            "pdf_downloader must import json at module level; otherwise "
            "every CDP helper silently returns False on NameError"
        )
        assert callable(pdl.json.loads)

    def test_cdp_check_connection_function_has_json_in_globals(self):
        # Belt-and-suspenders: the function's __globals__ (= module dict)
        # must include `json`. Catches weird import-order regressions that
        # might keep `pdl.json` assigned to something else.
        from citationclaw.core.pdf_downloader import _cdp_check_connection
        assert "json" in _cdp_check_connection.__globals__

    def test_cdp_open_login_pages_returns_int(self, tmp_path):
        # New helper added 2026-04-20 for Phase 2 login checkpoint. On a
        # port with no CDP server, must return 0 (no crash, no exception).
        from citationclaw.core.pdf_downloader import _cdp_open_login_pages
        # Port 65500 is unlikely to have a listener; connection check fails
        # fast and the helper returns 0.
        n = _cdp_open_login_pages(65500, ["https://example.com"])
        assert n == 0
        # Empty URL list also returns 0 without touching the network.
        n = _cdp_open_login_pages(9222, [])
        assert n == 0

    def test_debug_browser_profile_dir_is_absolute(self):
        # 2026-04-20: `_cdp_ensure_browser` used to call
        # `Path("runtime/debug_browser_profile")` which resolved against
        # the process CWD. Harness (CWD=eval_toolkit/phase12_harness/)
        # would therefore create a sibling profile, so login cookies from
        # the FastAPI-UI-spawned Chrome (CWD=v2 project root) were invisible
        # to harness runs and vice versa. Anchored to DATA_DIR.parent /
        # runtime since 2026-04-20. Guard the invariant.
        from citationclaw.core.pdf_downloader import DEBUG_BROWSER_PROFILE_DIR
        assert DEBUG_BROWSER_PROFILE_DIR.is_absolute(), (
            "DEBUG_BROWSER_PROFILE_DIR must be absolute; relative paths "
            "silently fragment publisher cookies across process CWDs"
        )
        # Must live under the v2 project root, not under some
        # tangentially-related CWD.
        s = str(DEBUG_BROWSER_PROFILE_DIR).replace("\\", "/")
        assert "/runtime/debug_browser_profile" in s, (
            f"unexpected profile location: {DEBUG_BROWSER_PROFILE_DIR}"
        )

    def test_cdp_ensure_browser_uses_absolute_profile(self):
        # Paranoid source-level check: the function body must reference
        # the module-level constant, not the old relative literal.
        from citationclaw.core.pdf_downloader import _cdp_ensure_browser
        import inspect
        src = inspect.getsource(_cdp_ensure_browser)
        assert "DEBUG_BROWSER_PROFILE_DIR" in src, (
            "_cdp_ensure_browser must use the module-level absolute constant"
        )
        assert 'Path("runtime/debug_browser_profile")' not in src, (
            "old relative literal reintroduced — would refragment profiles"
        )


class TestCdpLoginProbe:
    """2026-04-20: CDP per-publisher probe (core.cdp_login_probe).

    The probe runs in two contexts (standalone CLI + inline from
    TaskExecutor._prompt_phase2_login). These tests lock the public API
    surface both contexts rely on.
    """

    def test_probe_module_exposes_public_api(self):
        from citationclaw.core import cdp_login_probe as pr
        # Function signatures touched by both the CLI wrapper and
        # task_executor; guard their existence.
        assert callable(pr.probe_all)
        assert callable(pr.format_summary)
        assert isinstance(pr.PUBLISHER_PROBES, dict)
        # 5 publishers, each with the 4 required fields.
        assert set(pr.PUBLISHER_PROBES.keys()) == {
            "ieee", "acm", "elsevier", "springer", "wiley",
        }
        for pub, spec in pr.PUBLISHER_PROBES.items():
            assert {"doi", "title", "landing_url", "pdf_url"} <= set(spec)
        # Status constants consumers filter on.
        assert pr.STATUS_PDF_OK == "PDF_OK"
        assert pr.STATUS_AUTH_OK == "AUTH_OK"
        assert pr.STATUS_LOGIN_WALL == "LOGIN_WALL"
        assert pr.STATUS_FIXTURE_BROKEN == "FIXTURE_BROKEN"
        assert pr.STATUS_ERROR == "ERROR"
        # PASSING set must include BOTH flavors of auth-success.
        assert {pr.STATUS_PDF_OK, pr.STATUS_AUTH_OK} <= pr.PASSING_STATUSES

    def test_probe_all_rejects_unknown_publisher(self):
        from citationclaw.core.cdp_login_probe import probe_all
        import pytest
        with pytest.raises(ValueError, match="unknown publisher"):
            probe_all(9999, publishers=["bogus"])

    def test_probe_all_returns_error_results_on_dead_port(self):
        # Port 65501 has no CDP server -> each probe MUST return
        # STATUS_ERROR (no raise, no hang beyond the 3s check_connection
        # timeout). Guards against a future refactor that, say, makes
        # probe_all raise on unreachable port (would crash
        # _run_phase2_login_probe).
        from citationclaw.core.cdp_login_probe import (
            probe_all, STATUS_ERROR, PUBLISHER_PROBES,
        )
        # Only one publisher to keep test fast (each _cdp_check_connection
        # on an absent port eats ~3s).
        results = probe_all(65501, publishers=["ieee"], wait_s=0.1)
        assert len(results) == 1
        assert results[0].status == STATUS_ERROR
        assert "65501" in results[0].detail

    def test_format_summary_handles_mixed_results(self):
        from citationclaw.core.cdp_login_probe import (
            format_summary, ProbeResult,
            STATUS_PDF_OK, STATUS_AUTH_OK, STATUS_LOGIN_WALL,
        )
        rs = [
            ProbeResult("ieee", STATUS_PDF_OK),
            ProbeResult("acm", STATUS_PDF_OK),
            ProbeResult("elsevier", STATUS_AUTH_OK),
            ProbeResult("springer", STATUS_LOGIN_WALL),
        ]
        out = format_summary(rs)
        # deterministic alphabetic ordering; counts sum to input size
        assert "AUTH_OK:1" in out
        assert "LOGIN_WALL:1" in out
        assert "PDF_OK:2" in out

    def test_task_executor_calls_probe_when_flag_on(self):
        # Source-inspection guard: _run_phase2_login_probe must exist
        # and _prompt_phase2_login must call it (guarded by
        # enable_phase2_login_probe flag). Prevents a refactor from
        # silently dropping the post-login diagnostic step.
        from citationclaw.app.task_executor import TaskExecutor
        import inspect
        assert hasattr(TaskExecutor, "_run_phase2_login_probe")
        src = inspect.getsource(TaskExecutor._prompt_phase2_login)
        assert "_run_phase2_login_probe" in src, (
            "_prompt_phase2_login must invoke the probe after the "
            "login checkpoint (gated by enable_phase2_login_probe)"
        )
        assert "enable_phase2_login_probe" in src, (
            "probe call must be gated by the config flag"
        )

    def test_app_config_has_enable_phase2_login_probe(self):
        # ConfigUpdate / AppConfig plumbing for the new flag.
        from citationclaw.app.config_manager import AppConfig
        from citationclaw.app.main import ConfigUpdate
        c = AppConfig()
        assert c.enable_phase2_login_probe is True, (
            "default should be True so users get the diagnostic "
            "for free when CDP is enabled"
        )
        assert "enable_phase2_login_probe" in ConfigUpdate.model_fields

    def test_probe_exposes_captcha_status(self):
        # 2026-04-20: Cloudflare "Just a moment" detection. Status must
        # exist, have an icon, and NOT be in the PASSING_STATUSES set
        # (a captcha-blocked page means the real download will hang).
        from citationclaw.core import cdp_login_probe as pr
        assert pr.STATUS_CAPTCHA == "CAPTCHA"
        r = pr.ProbeResult("test", pr.STATUS_CAPTCHA)
        assert r.icon() == "[CAPTCHA]"
        assert not r.passed
        assert pr.STATUS_CAPTCHA not in pr.PASSING_STATUSES, (
            "CAPTCHA pages block the real download path even if auth is "
            "technically fine -- must not count toward 'passed'"
        )

    def test_probe_captcha_markers_cover_known_challenge_pages(self):
        # Module-level constants are the contract. Guards against
        # someone 'simplifying' the detection and accidentally dropping
        # the Chinese Cloudflare title that motivated this feature.
        from citationclaw.core import cdp_login_probe as pr
        assert "just a moment" in pr._CAPTCHA_TITLE_MARKERS
        assert "\u8bf7\u7a0d\u5019" in pr._CAPTCHA_TITLE_MARKERS  # "请稍候"
        assert "checking your browser" in pr._CAPTCHA_TITLE_MARKERS
        assert "verify you are human" in pr._CAPTCHA_TITLE_MARKERS
        # URL markers cover the CDN iframe host that the Turnstile
        # widget redirects to when challenged.
        assert any("challenge-platform" in m for m in pr._CAPTCHA_URL_MARKERS)

    def test_probe_body_detects_cloudflare_title_over_login(self):
        # When a page redirects through Cloudflare first (title = "请稍候"
        # / "Just a moment") the CAPTCHA branch must fire BEFORE the
        # login-wall / fixture-broken branches -- otherwise the classic
        # Elsevier case is misreported as AUTH_OK and caller burns 120s
        # in the real download path.
        from citationclaw.core import cdp_login_probe as pr
        import inspect
        src = inspect.getsource(pr._probe_one)
        # Ordering invariant: CAPTCHA return must precede login_wall return
        # in the function body. Simple substring-position check.
        i_captcha = src.find("STATUS_CAPTCHA")
        i_login = src.find("STATUS_LOGIN_WALL")
        assert 0 < i_captcha < i_login, (
            f"CAPTCHA branch must come before LOGIN_WALL branch in "
            f"_probe_one; got captcha@{i_captcha} login@{i_login}"
        )


class TestPhase2LoginStamp:
    """2026-04-20: sentinel file that lets returning users skip the
    180s login checkpoint when they've already logged in within the
    past N hours. File path is `<DEBUG_BROWSER_PROFILE_DIR>/
    phase2_login_stamp.json`; TTL is `config.phase2_login_stamp_hours`.
    """

    def test_config_has_phase2_login_stamp_hours(self):
        from citationclaw.app.config_manager import AppConfig
        from citationclaw.app.main import ConfigUpdate
        c = AppConfig()
        assert c.phase2_login_stamp_hours == 24, (
            "default TTL should be 24h -- typical daily-cadence user "
            "runs the pipeline twice in a day, shouldn't wait 180s twice"
        )
        assert "phase2_login_stamp_hours" in ConfigUpdate.model_fields

    def test_stamp_helpers_exist_on_task_executor(self):
        from citationclaw.app.task_executor import TaskExecutor
        # Three instance helpers wire the sentinel into the checkpoint.
        assert hasattr(TaskExecutor, "_phase2_stamp_path")
        assert hasattr(TaskExecutor, "_phase2_stamp_is_fresh")
        assert hasattr(TaskExecutor, "_phase2_stamp_write")

    def test_stamp_fresh_returns_false_when_missing(self, tmp_path, monkeypatch):
        # Point DEBUG_BROWSER_PROFILE_DIR at a tmp dir with no stamp file,
        # helper must return (False, None, None) without raising.
        from citationclaw.core import pdf_downloader as pdl
        from citationclaw.app.task_executor import TaskExecutor
        from citationclaw.app.log_manager import LogManager
        from citationclaw.app.config_manager import ConfigManager
        monkeypatch.setattr(pdl, "DEBUG_BROWSER_PROFILE_DIR", tmp_path)
        te = TaskExecutor(LogManager(), ConfigManager())
        fresh, data, age = te._phase2_stamp_is_fresh(24)
        assert fresh is False and data is None and age is None

    def test_stamp_fresh_returns_true_within_ttl(self, tmp_path, monkeypatch):
        import json, time as _t
        from datetime import datetime, timedelta
        from citationclaw.core import pdf_downloader as pdl
        from citationclaw.app.task_executor import TaskExecutor
        from citationclaw.app.log_manager import LogManager
        from citationclaw.app.config_manager import ConfigManager
        monkeypatch.setattr(pdl, "DEBUG_BROWSER_PROFILE_DIR", tmp_path)
        # Write a stamp dated 1h ago
        stamp = tmp_path / "phase2_login_stamp.json"
        stamp.write_text(json.dumps({
            "timestamp": (datetime.now() - timedelta(hours=1)).isoformat(),
            "outcome": "user_confirmed",
            "urls": [],
        }), encoding="utf-8")
        te = TaskExecutor(LogManager(), ConfigManager())
        fresh, data, age = te._phase2_stamp_is_fresh(ttl_hours=24)
        assert fresh is True
        assert 0.9 < age < 1.1
        assert data["outcome"] == "user_confirmed"

    def test_stamp_fresh_returns_false_when_stale(self, tmp_path, monkeypatch):
        import json
        from datetime import datetime, timedelta
        from citationclaw.core import pdf_downloader as pdl
        from citationclaw.app.task_executor import TaskExecutor
        from citationclaw.app.log_manager import LogManager
        from citationclaw.app.config_manager import ConfigManager
        monkeypatch.setattr(pdl, "DEBUG_BROWSER_PROFILE_DIR", tmp_path)
        stamp = tmp_path / "phase2_login_stamp.json"
        stamp.write_text(json.dumps({
            "timestamp": (datetime.now() - timedelta(hours=48)).isoformat(),
            "outcome": "user_confirmed",
            "urls": [],
        }), encoding="utf-8")
        te = TaskExecutor(LogManager(), ConfigManager())
        fresh, data, age = te._phase2_stamp_is_fresh(ttl_hours=24)
        assert fresh is False
        assert age is not None and age > 24

    def test_stamp_ttl_zero_always_returns_false(self, tmp_path, monkeypatch):
        # TTL=0 is the "always prompt" escape hatch. Even a fresh stamp
        # must not short-circuit.
        import json
        from datetime import datetime
        from citationclaw.core import pdf_downloader as pdl
        from citationclaw.app.task_executor import TaskExecutor
        from citationclaw.app.log_manager import LogManager
        from citationclaw.app.config_manager import ConfigManager
        monkeypatch.setattr(pdl, "DEBUG_BROWSER_PROFILE_DIR", tmp_path)
        stamp = tmp_path / "phase2_login_stamp.json"
        stamp.write_text(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "outcome": "user_confirmed",
            "urls": [],
        }), encoding="utf-8")
        te = TaskExecutor(LogManager(), ConfigManager())
        fresh, _, _ = te._phase2_stamp_is_fresh(ttl_hours=0)
        assert fresh is False

    def test_stamp_fresh_handles_corrupt_json_gracefully(self, tmp_path, monkeypatch):
        from citationclaw.core import pdf_downloader as pdl
        from citationclaw.app.task_executor import TaskExecutor
        from citationclaw.app.log_manager import LogManager
        from citationclaw.app.config_manager import ConfigManager
        monkeypatch.setattr(pdl, "DEBUG_BROWSER_PROFILE_DIR", tmp_path)
        stamp = tmp_path / "phase2_login_stamp.json"
        stamp.write_text("this is not json", encoding="utf-8")
        te = TaskExecutor(LogManager(), ConfigManager())
        fresh, data, age = te._phase2_stamp_is_fresh(ttl_hours=24)
        # Corrupt file = treat as absent. Never raise.
        assert fresh is False and data is None and age is None

    def test_stamp_write_creates_file_with_correct_schema(self, tmp_path, monkeypatch):
        import json
        from citationclaw.core import pdf_downloader as pdl
        from citationclaw.app.task_executor import TaskExecutor
        from citationclaw.app.log_manager import LogManager
        from citationclaw.app.config_manager import ConfigManager
        monkeypatch.setattr(pdl, "DEBUG_BROWSER_PROFILE_DIR", tmp_path)
        te = TaskExecutor(LogManager(), ConfigManager())
        te._phase2_stamp_write("user_confirmed", ["https://a.example", "https://b.example"])
        stamp = tmp_path / "phase2_login_stamp.json"
        assert stamp.exists()
        data = json.loads(stamp.read_text(encoding="utf-8"))
        assert data["outcome"] == "user_confirmed"
        assert data["urls"] == ["https://a.example", "https://b.example"]
        assert "timestamp" in data

    def test_prompt_phase2_login_short_circuits_via_stamp(self):
        # Source-level guard: the checkpoint method must check the
        # sentinel before opening tabs / broadcasting. If someone
        # refactors the method and accidentally drops the short-circuit,
        # old users would be back to 180s-a-run.
        from citationclaw.app.task_executor import TaskExecutor
        import inspect
        src = inspect.getsource(TaskExecutor._prompt_phase2_login)
        assert "_phase2_stamp_is_fresh" in src
        assert "_phase2_stamp_write" in src
        # The freshness check must happen before the tab-open CALL SITE.
        # Anchor on "opened = _cdp_open_login_pages" (unique call pattern)
        # NOT on the import-statement occurrence near the top.
        i_check = src.find("_phase2_stamp_is_fresh")
        i_open_call = src.find("opened = _cdp_open_login_pages")
        assert 0 < i_check < i_open_call, (
            "sentinel freshness check must precede the tab-open call "
            f"(check@{i_check} call@{i_open_call})"
        )
        # Stamp MUST be written at some point (captures post-checkpoint
        # result so next run can skip).
        assert "_phase2_stamp_write" in src


class TestPhase2UiWiring:
    """2026-04-20: end-to-end FastAPI + WebSocket wiring for the Phase 2
    login modal. Three front-end assets need to be verified in lockstep
    with the backend integration:

    1. `index.html` renders the `phase2LoginModal` div with the expected
       Bootstrap attributes + button IDs.
    2. `main.js` contains a `ws.on('phase2_login_prompt', ...)` handler
       that uses the same button IDs and POSTs to
       `/api/task/phase2-login-ready`.
    3. A live WS client receives the `phase2_login_prompt` event when
       `log_manager.broadcast_event(...)` is invoked.

    Without these tests passing, a harness run could look great in the
    logs (WebSocket event broadcast succeeds server-side) while the
    actual browser modal silently fails to pop.
    """

    def _make_testclient(self):
        from fastapi.testclient import TestClient
        from citationclaw.app.main import app
        return TestClient(app)

    def test_index_html_contains_phase2_login_modal(self):
        client = self._make_testclient()
        r = client.get("/")
        assert r.status_code == 200
        body = r.text
        # Modal div + the two action buttons rendered by the Bootstrap
        # template. These IDs are the contract main.js relies on.
        assert 'id="phase2LoginModal"' in body, (
            "index.html must render the phase2LoginModal Bootstrap modal"
        )
        assert 'id="p2l-btn-continue"' in body, (
            'The "已登录，继续 Phase 2" button (id=p2l-btn-continue) must exist'
        )
        assert 'id="p2l-btn-skip"' in body, (
            'The "跳过，直接继续" button (id=p2l-btn-skip) must exist'
        )
        assert 'id="p2l-countdown"' in body, (
            "countdown element must exist so the JS handler can fill it in"
        )
        assert 'id="p2l-url-list"' in body, (
            "url list <ul> must exist so the JS handler can render "
            "opened publisher URLs"
        )

    def test_main_js_has_phase2_login_prompt_handler(self):
        client = self._make_testclient()
        r = client.get("/static/js/main.js")
        assert r.status_code == 200
        js = r.text
        # The WebSocket event handler must be registered.
        assert "ws.on('phase2_login_prompt'" in js or \
               'ws.on("phase2_login_prompt"' in js, (
            "main.js must register a handler for the phase2_login_prompt "
            "WebSocket event"
        )
        # Must POST to the server-side unlock endpoint.
        assert "/api/task/phase2-login-ready" in js, (
            "main.js must POST to /api/task/phase2-login-ready when user "
            "clicks the 继续 / 跳过 button"
        )
        # All three DOM IDs referenced by the handler must match what
        # index.html renders -- test catches drift if either side changes.
        for dom_id in ("p2l-btn-continue", "p2l-btn-skip",
                       "p2l-countdown", "p2l-url-list",
                       "phase2LoginModal"):
            assert dom_id in js, (
                f"main.js must reference DOM id {dom_id!r} "
                "(must match index.html)"
            )

    def test_event_name_matches_between_server_and_frontend(self):
        """The WebSocket event name is the glue between server and
        browser. If the server broadcasts `"phase2_login_prompt"` but
        `main.js` listens for `"phase2_login"` (typo, rename, etc.),
        the modal silently never shows despite every log line looking
        normal server-side. This test locks the string in place.

        (We tried a live-WS integration test first but `TestClient` is
        sync while `log_manager._schedule_broadcast` relies on
        `asyncio.create_task` from a running loop -- the broadcast
        coroutine never got awaited in the test context. Source
        inspection is the pragmatic alternative.)
        """
        import inspect
        from citationclaw.app.task_executor import TaskExecutor
        # Server-side: _prompt_phase2_login must broadcast the exact
        # event-name string "phase2_login_prompt".
        be_src = inspect.getsource(TaskExecutor._prompt_phase2_login)
        assert 'broadcast_event("phase2_login_prompt"' in be_src, (
            "_prompt_phase2_login must broadcast the "
            '"phase2_login_prompt" event (exact string) -- main.js '
            "listens for this literal"
        )
        # And the payload must include the three fields the frontend
        # uses: urls (to render), wait_seconds (for countdown), cdp_port
        # (informational). Missing any breaks the modal.
        for field in ("urls", "wait_seconds", "cdp_port"):
            assert f'"{field}"' in be_src, (
                f'broadcast_event payload must include the "{field}" key'
            )

        # Frontend side: main.js file on disk references the same string.
        # This complements `test_main_js_has_phase2_login_prompt_handler`
        # which fetches it via HTTP -- here we read the raw file to
        # catch any divergence between TestClient's mount path and the
        # actual file layout.
        from pathlib import Path
        js_path = (Path(__file__).resolve().parent.parent
                   / "citationclaw" / "static" / "js" / "main.js")
        assert js_path.exists(), f"main.js missing at {js_path}"
        js_text = js_path.read_text(encoding="utf-8")
        assert "'phase2_login_prompt'" in js_text or \
               '"phase2_login_prompt"' in js_text, (
            "main.js must listen for the exact event name "
            '"phase2_login_prompt"'
        )

    def test_phase2_login_ready_endpoint_success_path(self):
        """Complements the existing "no-event armed -> 400" test.

        Arm a synthetic event on the task_executor, POST the endpoint,
        verify 200 + the event is set (so a real checkpoint await
        would immediately wake up).
        """
        import asyncio
        from citationclaw.app.main import app, task_executor
        from fastapi.testclient import TestClient
        client = TestClient(app)
        # Arm the event inline (sync context; asyncio.Event() is safe).
        task_executor._phase2_login_event = asyncio.Event()
        try:
            r = client.post("/api/task/phase2-login-ready")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "success"
            assert task_executor._phase2_login_event.is_set(), (
                "endpoint must call event.set() so a waiting "
                "_prompt_phase2_login unblocks"
            )
        finally:
            task_executor._phase2_login_event = None


class TestPdfFailureSurfacing:
    """2026-04-21: when a UI user runs a pipeline and sees dozens of
    cascade-internal `HTTP 403` / `Connection error` / `Cloudflare 验证`
    lines, they can't tell how many papers actually failed, nor can
    they reconstruct WHY any specific paper failed without manually
    greping through hundreds of interleaved INFO lines. Fix: each
    terminal failure now emits a self-contained ERROR-level diagnostic
    block containing the paper's DOI, detected publisher, and the
    *full* cascade trace (every INFO line the cascade emitted for
    THIS paper across its 1+2 attempts).

    These tests lock the wiring so a future refactor can't silently
    regress to "one-line generic failure message + 200 unrelated
    INFO lines to sift through".
    """

    def test_download_accepts_log_error_callback(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        sig = inspect.signature(PDFDownloader.download)
        assert "log_error" in sig.parameters, (
            "download() must accept a log_error keyword so terminal "
            "failures can go to LogManager.error (ERROR level), not "
            "just LogManager.info (INFO level)"
        )

    def test_download_tees_cascade_log_into_trace(self):
        # The per-paper failure trace is the whole point. Source-
        # inspection guard that download() collects cascade log lines
        # into a local list and passes a tee'd logger into
        # `_download_once`.
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader.download)
        assert "trace" in src and "_tee_log" in src, (
            "download() must wrap the log callback to also append "
            "each cascade line into a per-paper trace list"
        )
        # The tee'd logger must be what `_download_once` receives,
        # not the raw log callback. (2026-04-21: signature gained
        # log_ok kwarg, so check just the `log=_tee_log` fragment.)
        assert "log=_tee_log" in src, (
            "_download_once must be called with the teed logger so "
            "cascade lines land in the trace list"
        )
        assert "_download_once(" in src

    def test_download_dumps_trace_on_terminal_failure(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader.download)
        # The diagnostic block header must use [PDF失败] (greppable
        # tag), contain DOI, and mention the publisher.
        assert "[PDF失败]" in src
        assert "DOI=" in src, (
            "diagnostic block header must include the paper's DOI for "
            "quick triage"
        )
        assert "pub=" in src, (
            "diagnostic block header must include detected publisher "
            "so a glance tells you 'it's IEEE / Elsevier / ...'"
        )
        # Must iterate the collected trace and emit each line with the
        # `>> ` prefix so the block reads as a single coherent trace.
        assert ">>" in src, (
            "trace dump must prefix each line with '>>' for visual "
            "grouping in run.log"
        )
        # Must cap length via the class constant so a runaway tier doesn't
        # flood run.log.
        assert "_FAIL_TRACE_MAX_LINES" in src, (
            "trace dump must be capped at _FAIL_TRACE_MAX_LINES"
        )

    def test_fail_trace_max_lines_is_reasonable(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        # The cap should be large enough to cover the ~15-tier cascade
        # across 3 attempts (~45 lines upper bound) but not so huge
        # that a single paper's trace dominates run.log.
        assert 20 <= PDFDownloader._FAIL_TRACE_MAX_LINES <= 100, (
            f"_FAIL_TRACE_MAX_LINES = "
            f"{PDFDownloader._FAIL_TRACE_MAX_LINES} looks off; "
            "want somewhere between 20 (covers ~1 attempt) and 100 "
            "(covers any reasonable 3-attempt cascade without flood)"
        )

    def test_task_executor_wires_log_error_to_log_manager_error(self):
        from citationclaw.app.task_executor import TaskExecutor
        import inspect
        src = inspect.getsource(TaskExecutor._run_new_phase2_and_3)
        assert "log_error=self.log_manager.error" in src, (
            "downloader.download() must be called with "
            "log_error=self.log_manager.error so terminal PDF failures "
            "appear as ERROR in the UI / run.log"
        )

    def test_task_executor_emits_failure_summary_block(self):
        from citationclaw.app.task_executor import TaskExecutor
        import inspect
        src = inspect.getsource(TaskExecutor._run_new_phase2_and_3)
        assert "[PDF失败汇总]" in src, (
            "_run_new_phase2_and_3 must emit a [PDF失败汇总] scoreboard "
            "when any paper fails"
        )
        assert "failed_titles" in src
        # Must use error severity
        assert "self.log_manager.error(" in src

    def test_download_trace_dumps_via_log_error_not_log_info(self):
        # Cascade-internal INFO lines (HTTP 403, Connection error, ...)
        # are noisy but routine -- they stay at INFO. The terminal
        # failure block is different: it's ONE place where we summarize
        # everything that went wrong for a paper, and it must be ERROR
        # level so users grep `[ERROR]` to find all failures.
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader.download)
        # `emit = log_error if log_error else log` prefers log_error
        assert "emit = log_error if log_error else log" in src, (
            "terminal failure dump must prefer log_error over log"
        )


class TestGreenPathAndLogLevel:
    """2026-04-21: user wants a calmer UI -- the cascade's per-tier
    INFO chatter ('HTTP 403', 'Connection error', 'Cloudflare 验证')
    makes users feel the pipeline is broken even when it's succeeding.
    Two changes address this:

    1. [PDF OK] / [PDF缓存] messages emit at SUCCESS level (green in UI)
       via a new `log_ok` callback on PDFDownloader.download().
    2. A new config field `log_min_level` threshold-filters everything
       through LogManager: set to "SUCCESS" in config.json to drop the
       INFO noise UI-wide (still visible in debug mode by setting
       "INFO" back).

    These tests lock the plumbing so a future refactor can't silently
    regress either feature.
    """

    def test_download_accepts_log_ok_callback(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        sig = inspect.signature(PDFDownloader.download)
        assert "log_ok" in sig.parameters, (
            "download() must accept a log_ok keyword so [PDF OK] / "
            "[PDF缓存] can route through LogManager.success (green)"
        )

    def test_download_routes_cache_hit_through_log_ok(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader.download)
        # cache-hit path: `_emit_ok = log_ok if log_ok else log` then
        # `_emit_ok(f"    [PDF缓存] {title}")`.
        assert "_emit_ok = log_ok if log_ok else log" in src, (
            "download() must prefer log_ok for [PDF缓存] so UI paints "
            "it green"
        )
        assert "_emit_ok(f\"    [PDF缓存]" in src or \
               "_emit_ok(f'    [PDF缓存]" in src, (
            "cache-hit message must go through _emit_ok"
        )

    def test_download_once_ok_routes_pdf_ok_through_log_ok(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader._download_once)
        # Inside _ok() closure: `_emit = log_ok if log_ok else log` is
        # the key line, then _emit(f"    [PDF OK] ...").
        assert "_emit = log_ok if log_ok else log" in src, (
            "_ok() must prefer log_ok for the [PDF OK] message"
        )
        assert "[PDF OK]" in src

    def test_task_executor_passes_log_ok(self):
        from citationclaw.app.task_executor import TaskExecutor
        import inspect
        src = inspect.getsource(TaskExecutor._run_new_phase2_and_3)
        assert "log_ok=self.log_manager.success" in src, (
            "download() must be wired with log_ok=log_manager.success "
            "so [PDF OK] goes at SUCCESS level (green in UI)"
        )

    def test_log_manager_level_threshold_filter(self):
        from citationclaw.app.log_manager import LogManager
        lm = LogManager()
        # Default admits everything at INFO+.
        lm.info("noise at info")
        assert any(e["level"] == "INFO" for e in lm.logs), (
            "default threshold must admit INFO"
        )
        lm.clear_logs()
        # Bump threshold to SUCCESS. Now INFO should be dropped but
        # SUCCESS / WARNING / ERROR should pass.
        lm.set_min_level("SUCCESS")
        lm.info("should be dropped")
        lm.success("should be kept green")
        lm.warning("should be kept yellow")
        lm.error("should be kept red")
        levels = [e["level"] for e in lm.logs]
        assert "INFO" not in levels, (
            "threshold=SUCCESS must drop INFO messages entirely "
            "(no history, no file, no WebSocket)"
        )
        assert "SUCCESS" in levels
        assert "WARNING" in levels
        assert "ERROR" in levels

    def test_log_manager_threshold_case_insensitive_and_unknown_default(self):
        from citationclaw.app.log_manager import LogManager
        lm = LogManager()
        # Case-insensitive acceptance.
        lm.set_min_level("warning")
        lm.info("dropped")
        lm.success("dropped")
        lm.warning("kept")
        lm.error("kept")
        levels = [e["level"] for e in lm.logs]
        assert "INFO" not in levels and "SUCCESS" not in levels
        assert "WARNING" in levels
        # Unknown value -> default to INFO (permissive).
        lm.clear_logs()
        lm.set_min_level("not-a-level")
        lm.info("kept now")
        assert any(e["level"] == "INFO" for e in lm.logs)

    def test_app_config_has_log_min_level(self):
        from citationclaw.app.config_manager import AppConfig
        from citationclaw.app.main import ConfigUpdate
        c = AppConfig()
        assert c.log_min_level == "INFO", (
            "default must be INFO so existing users see zero behavior "
            "change (feature is opt-in via manual config.json edit)"
        )
        assert "log_min_level" in ConfigUpdate.model_fields

    def test_task_executor_applies_log_min_level_from_config(self):
        from citationclaw.app.task_executor import TaskExecutor
        import inspect
        src = inspect.getsource(TaskExecutor._run_new_phase2_and_3)
        assert "set_min_level" in src, (
            "_run_new_phase2_and_3 must apply config.log_min_level via "
            "LogManager.set_min_level so the threshold actually kicks in"
        )
        assert "log_min_level" in src


class TestUrlFinderEmptyKeys:
    """2026-04-21: observed in a UI smoke-test run -- the URL finder
    raised `ZeroDivisionError: integer modulo by zero` from
    `self.api_keys[self.key_idx % len(self.api_keys)]` when
    config.scraper_api_keys had been silently wiped to `[]` by a UI
    save. Root cause (silent-wipe) is being fixed by adding
    scraper_api_keys to the ConfigUpdate sensitive-preservation list,
    but the URL finder should ALSO fail with a descriptive error so
    users can diagnose without reading Python stack traces.
    """

    def test_empty_keys_raises_descriptive_runtime_error(self):
        from citationclaw.core.url_finder import PaperURLFinder
        import pytest
        finder = PaperURLFinder(api_keys=[])
        with pytest.raises(RuntimeError, match="scraper_api_keys"):
            finder._next_key()

    def test_config_preservation_protects_scraper_api_keys(self):
        from citationclaw.app.main import save_config
        import inspect
        src = inspect.getsource(save_config)
        # scraper_api_keys + openai_base_url + openai_model must be in
        # the sensitive-preservation list so an empty UI POST can't
        # overwrite a configured value.
        for key in ("scraper_api_keys", "openai_base_url", "openai_model"):
            assert f'"{key}"' in src, (
                f"{key!r} must be in save_config's sensitive-preservation "
                "list to prevent UI silent-wipe. Symptoms: empty list -> "
                "ZeroDivisionError in PaperURLFinder; empty strings -> "
                "OpenAI client construction failures"
            )


class TestArxivDoiRecognition:
    """2026-04-21: observed PoolNet+ failure with DOI=10.48550/arxiv.
    2512.05362. No cascade tier recognized the 10.48550 prefix as
    arXiv, so arxiv_id was never set, arXiv tier was skipped, and
    the paper fell through to LLM search (dead) -> failure. Lock
    the new recognition so it can't regress.
    """

    def test_publisher_from_doi_recognizes_arxiv_prefix(self):
        from citationclaw.core.pdf_downloader import _publisher_from_doi
        assert _publisher_from_doi("10.48550/arXiv.2512.05362") == "arxiv"
        assert _publisher_from_doi("10.48550/arxiv.2512.05362") == "arxiv"
        # Existing prefixes unaffected
        assert _publisher_from_doi("10.1109/TPAMI.2023.12345") == "ieee"
        assert _publisher_from_doi("10.1016/j.patcog.2023.100") == "elsevier"

    def test_arxiv_id_from_doi_helper(self):
        from citationclaw.core.pdf_downloader import _arxiv_id_from_doi
        assert _arxiv_id_from_doi("10.48550/arxiv.2512.05362") == "2512.05362"
        # Version suffix stripped
        assert _arxiv_id_from_doi("10.48550/arXiv.2301.12345v2") == "2301.12345"
        # 5-digit paper id
        assert _arxiv_id_from_doi("10.48550/arxiv.2206.12345") == "2206.12345"
        # Non-arXiv DOI -> None
        assert _arxiv_id_from_doi("10.1109/TPAMI.2023.12345") is None
        assert _arxiv_id_from_doi("") is None
        assert _arxiv_id_from_doi(None) is None

    def test_download_once_extracts_arxiv_id_from_doi(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader._download_once)
        # The extraction must happen BEFORE the arXiv cascade tier so
        # arxiv_id is set when that tier runs.
        assert "_arxiv_id_from_doi" in src
        assert "arxiv_from_doi" in src


class TestCdpElsevierCircuitBreaker:
    """2026-04-21: observed run 2026-04-21 01:25 -- 70 CDP-Elsevier
    Cloudflare timeouts, 0 successes, ~8 minutes of wait time. The
    Turnstile challenge needs manual user interaction; if the user
    isn't available, waiting out the 120s timer per paper is waste.
    Circuit breaker pattern (same shape as the V-API 2026-04-20
    `_llm_search_429_misses` breaker) disables the tier after N
    consecutive CF timeouts.
    """

    def test_downloader_exposes_circuit_breaker_state(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        dl = PDFDownloader(scraper_api_keys=["k"])
        assert hasattr(dl, "_cdp_elsevier_cf_timeouts")
        assert hasattr(dl, "_cdp_elsevier_disabled")
        assert dl._cdp_elsevier_cf_timeouts == 0
        assert dl._cdp_elsevier_disabled is False
        assert PDFDownloader._CDP_ELSEVIER_MAX_CF_TIMEOUTS == 3

    def test_cdp_elsevier_short_circuits_when_disabled(self):
        import asyncio
        from citationclaw.core.pdf_downloader import PDFDownloader
        dl = PDFDownloader(scraper_api_keys=["k"], cdp_debug_port=9222)
        dl._cdp_elsevier_disabled = True
        # paper dict with a valid /pii/ link so we'd normally proceed
        paper = {
            "paper_link": "https://www.sciencedirect.com/science/article/pii/S1566253524005840",
            "doi": "10.1016/j.inffus.2024.102806",
        }
        captured = []
        result = asyncio.get_event_loop().run_until_complete(
            dl._try_cdp_elsevier(paper, log=lambda s: captured.append(s))
        )
        assert result is None
        assert any("电路断路器" in s for s in captured), (
            "short-circuit path must log why it skipped"
        )

    def test_cdp_elsevier_has_cf_box_and_counter_logic(self):
        # Source-level check that the counter-increment logic is in
        # place (hard to exercise the full async flow without a live
        # Chrome). Guards against refactors that drop the tracking.
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader._try_cdp_elsevier)
        assert "_hit_cf_box" in src, (
            "must track whether the attempt hit Cloudflare"
        )
        assert "_cdp_elsevier_cf_timeouts += 1" in src, (
            "must increment the CF-timeout counter on Cloudflare-caused "
            "failure"
        )
        assert "_cdp_elsevier_cf_timeouts = 0" in src, (
            "must reset the counter on success"
        )
        assert "_cdp_elsevier_disabled = True" in src, (
            "must actually flip the breaker after threshold"
        )


class TestElsevierPacingAndCooldown:
    """2026-04-21: user reported SD's own risk control triggers when
    5 concurrent workers all navigate SD tabs at once AND when tabs
    switch too fast. Mitigations:
      1. Instance semaphore (concurrency=1 for SD work)
      2. Minimum inter-request gap of 15s
      3. 5-minute cooldown after any Cloudflare hit
    These tests lock the new state + config constants so a refactor
    can't silently regress.
    """

    def test_constants_set_to_reasonable_values(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        assert PDFDownloader._ELSEVIER_MIN_GAP_S >= 10, (
            "pacing gap too short risks triggering SD's rate limiter"
        )
        assert PDFDownloader._ELSEVIER_MIN_GAP_S <= 60, (
            "pacing gap too long makes batch runs take forever"
        )
        assert PDFDownloader._ELSEVIER_COOLDOWN_S >= 60, (
            "cooldown too short means we re-hit the CF window before "
            "SD forgets us"
        )
        assert PDFDownloader._ELSEVIER_COOLDOWN_S <= 900, (
            "cooldown longer than 15min is overkill"
        )

    def test_downloader_exposes_pacing_state(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        dl = PDFDownloader(scraper_api_keys=["k"])
        # Semaphore is lazy-init'd (None until first SD request)
        assert dl._elsevier_sem is None
        assert dl._elsevier_last_request_at == 0.0
        assert dl._elsevier_cooldown_until == 0.0

    def test_cdp_elsevier_skips_during_cooldown(self):
        import asyncio
        from citationclaw.core.pdf_downloader import PDFDownloader
        dl = PDFDownloader(scraper_api_keys=["k"], cdp_debug_port=9222)
        # Put us in the middle of a cooldown window.
        loop = asyncio.new_event_loop()
        dl._elsevier_cooldown_until = loop.time() + 300
        paper = {
            "paper_link": "https://www.sciencedirect.com/science/article/pii/S1234567890",
            "doi": "10.1016/j.test.2024.1",
        }
        captured = []
        result = loop.run_until_complete(
            dl._try_cdp_elsevier(paper, log=lambda s: captured.append(s))
        )
        loop.close()
        assert result is None
        assert any("SD 冷却中" in s for s in captured), (
            "cooldown short-circuit must log why it skipped"
        )

    def test_cf_hit_sets_cooldown(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader._try_cdp_elsevier)
        # The CF-hit branch must set the cooldown timestamp.
        assert "_elsevier_cooldown_until =" in src, (
            "CF hit must populate _elsevier_cooldown_until so future "
            "attempts skip SD during the cooldown window"
        )
        assert "_ELSEVIER_COOLDOWN_S" in src

    def test_cdp_elsevier_uses_semaphore_and_pacing(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader._try_cdp_elsevier)
        # Semaphore acquire wrapping the _sync call
        assert "async with self._elsevier_sem" in src, (
            "CDP-Elsevier must serialize via _elsevier_sem to keep "
            "concurrency=1"
        )
        # Min-gap enforcement before the _sync call
        assert "_ELSEVIER_MIN_GAP_S" in src
        assert "_elsevier_last_request_at" in src

    def test_pdf_viewer_timeout_marks_cf_hit(self):
        from citationclaw.core.pdf_downloader import PDFDownloader
        import inspect
        src = inspect.getsource(PDFDownloader._try_cdp_elsevier)
        # The second wait loop (PDF viewer appearance) times out at
        # deadline_pdf. 95%+ of the time that's CF holding pdfft;
        # mark it so the outer wrapper triggers cooldown.
        # Look for the pattern: loop ends -> mark _hit_cf_box -> return
        assert '_hit_cf_box["saw"] = True' in src, (
            "the _hit_cf_box flag must be settable"
        )
        # The viewer-timeout branch should set it too.
        viewer_loop_idx = src.find("Wait for PDF viewer")
        next_return_none_idx = src.find("return None",
                                         viewer_loop_idx + 1)
        # Between the viewer-loop comment and the return None that
        # follows, there should be the _hit_cf_box["saw"] = True line.
        assert '_hit_cf_box["saw"] = True' in src[
            viewer_loop_idx:next_return_none_idx + 20
        ], (
            "PDF-viewer-never-appeared timeout must mark _hit_cf_box "
            "so SD cooldown triggers (viewer stalls are CF in 95%+ "
            "of observed cases)"
        )
