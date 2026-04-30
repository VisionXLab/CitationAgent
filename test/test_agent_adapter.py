import json
import shutil
from pathlib import Path

from citationclaw.agent import (
    AgentPaperInput,
    AgentRunRequest,
    build_agent_config,
    list_result_folders,
    read_result_summary,
    run_agent_request,
)


def test_env_keys_are_loaded_without_persisting(monkeypatch, tmp_path):
    monkeypatch.setenv("CITATIONCLAW_SCRAPER_API_KEYS", "scraper-a, scraper-b")
    monkeypatch.setenv("CITATIONCLAW_OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("CITATIONCLAW_OPENAI_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("CITATIONCLAW_OPENAI_MODEL", "search-model")

    config_path = tmp_path / "config.json"
    request = AgentRunRequest(
        papers=[AgentPaperInput(title="Target Paper")],
        config_path=str(config_path),
    )

    config = build_agent_config(request)

    assert config.scraper_api_keys == ["scraper-a", "scraper-b"]
    assert config.openai_api_key == "openai-key"
    assert config.openai_base_url == "https://api.example.test/v1"
    assert config.openai_model == "search-model"
    assert not config_path.exists()


def test_headless_test_mode_run_returns_machine_readable_result(tmp_path, monkeypatch):
    fixture_dir = tmp_path / "test"
    fixture_dir.mkdir()
    shutil.copy2(
        Path(__file__).parent / "mock_author_info.jsonl",
        fixture_dir / "mock_author_info.jsonl",
    )
    monkeypatch.chdir(tmp_path)

    request = AgentRunRequest(
        papers=[AgentPaperInput(title="Target Paper", aliases=["Target Alias"])],
        output_prefix="agent",
        config_overrides={
            "test_mode": True,
            "enable_citing_description": False,
            "enable_dashboard": False,
        },
    )

    result = run_agent_request(request)

    assert result["status"] == "success"
    assert result["result_dir"].startswith("data/result-")
    assert result["outputs"]["excel"].endswith("agent_results.xlsx")
    assert result["outputs"]["json"].endswith("agent_results.json")
    assert Path(result["outputs"]["excel"]).exists()
    assert Path(result["outputs"]["json"]).exists()
    assert result["cost_summary"]["scraper_requests"] == 0
    assert result["logs"]


def test_result_listing_and_summary(tmp_path):
    result_dir = tmp_path / "data" / "result-20260430_120000"
    result_dir.mkdir(parents=True)
    (result_dir / "paper_results.json").write_text(
        json.dumps([{"Paper_Title": "A"}, {"Paper_Title": "B"}]),
        encoding="utf-8",
    )
    (result_dir / "paper_dashboard.html").write_text("<html></html>", encoding="utf-8")

    folders = list_result_folders(tmp_path / "data")
    summary = read_result_summary(result_dir)

    assert folders[0]["name"] == "result-20260430_120000"
    assert summary["result_dir"] == str(result_dir)
    assert summary["record_count"] == 2
    assert summary["files"]["dashboard"].endswith("paper_dashboard.html")
