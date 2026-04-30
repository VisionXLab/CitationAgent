from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field

from citationclaw.app.config_manager import (
    AppConfig,
    ConfigManager,
    SERVICE_TIER_PRESETS,
)
from citationclaw.app.task_executor import TaskExecutor


class AgentPaperInput(BaseModel):
    title: str
    aliases: list[str] = Field(default_factory=list)


class AgentRunRequest(BaseModel):
    papers: list[AgentPaperInput]
    output_prefix: str = "paper"
    service_tier: str | None = None
    config_path: str | None = None
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    working_dir: str | None = None
    logs_tail: int = 100


class AgentLogCollector:
    def __init__(self):
        self.logs: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.current_progress = {"current": 0, "total": 100, "percentage": 0}

    def _record(self, level: str, message: str):
        self.logs.append({"level": level, "message": str(message)})

    def info(self, message: str):
        self._record("INFO", message)

    def success(self, message: str):
        self._record("SUCCESS", message)

    def warning(self, message: str):
        self._record("WARNING", message)

    def error(self, message: str):
        self._record("ERROR", message)

    def update_progress(self, current: int, total: int):
        percentage = int((current / total) * 100) if total > 0 else 0
        self.current_progress = {
            "current": current,
            "total": total,
            "percentage": percentage,
        }
        self.events.append({"type": "progress", "data": self.current_progress})

    def broadcast_event(self, event_type: str, payload: dict):
        self.events.append({"type": event_type, "data": payload})

    async def _broadcast(self, message: dict):
        self.events.append(message)

    def get_recent_logs(self, count: int = 100):
        return self.logs[-count:]


def _split_keys(value: str) -> list[str]:
    parts = value.replace("\n", ",").replace(";", ",").split(",")
    return [part.strip() for part in parts if part.strip()]


def _first_env(names: Iterable[str]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _apply_service_tier(config_data: dict[str, Any], service_tier: str | None):
    if not service_tier:
        return
    preset = SERVICE_TIER_PRESETS.get(service_tier)
    if not preset:
        raise ValueError(f"Unknown service_tier: {service_tier}")
    config_data["service_tier"] = service_tier
    config_data.update(preset.get("switches", {}))


def build_agent_config(request: AgentRunRequest) -> AppConfig:
    manager = ConfigManager(request.config_path or "config.json")
    config_data = manager.get().model_dump()

    _apply_service_tier(config_data, request.service_tier)
    config_data.update(request.config_overrides)

    scraper_keys = _first_env(["CITATIONCLAW_SCRAPER_API_KEYS", "SCRAPERAPI_KEYS", "SCRAPERAPI_KEY"])
    if scraper_keys:
        config_data["scraper_api_keys"] = _split_keys(scraper_keys)

    env_overrides = {
        "openai_api_key": _first_env(["CITATIONCLAW_OPENAI_API_KEY", "OPENAI_API_KEY"]),
        "openai_base_url": _first_env(["CITATIONCLAW_OPENAI_BASE_URL", "OPENAI_BASE_URL"]),
        "openai_model": _first_env(["CITATIONCLAW_OPENAI_MODEL", "OPENAI_MODEL"]),
        "api_access_token": _first_env(["CITATIONCLAW_API_ACCESS_TOKEN"]),
        "api_user_id": _first_env(["CITATIONCLAW_API_USER_ID"]),
    }
    for key, value in env_overrides.items():
        if value:
            config_data[key] = value

    return AppConfig(**config_data)


def validate_agent_config(request: AgentRunRequest) -> dict[str, Any]:
    config = build_agent_config(request)
    missing = []
    if not config.test_mode:
        if not config.scraper_api_keys:
            missing.append("scraper_api_keys")
        if not config.openai_api_key:
            missing.append("openai_api_key")
        if not config.openai_base_url:
            missing.append("openai_base_url")
    return {
        "ok": not missing,
        "test_mode": config.test_mode,
        "missing": missing,
        "service_tier": config.service_tier,
        "scraper_api_keys_count": len(config.scraper_api_keys),
        "openai_base_url_configured": bool(config.openai_base_url),
        "openai_model": config.openai_model,
    }


@contextmanager
def _maybe_chdir(path: str | None):
    if not path:
        yield
        return
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


def _normalize_paper_groups(papers: list[AgentPaperInput]) -> list[dict[str, Any]]:
    groups = []
    for paper in papers:
        title = paper.title.strip()
        if not title:
            continue
        groups.append({
            "title": title,
            "aliases": [alias.strip() for alias in paper.aliases if alias.strip()],
        })
    if not groups:
        raise ValueError("At least one paper title is required.")
    return groups


async def run_agent_request_async(request: AgentRunRequest) -> dict[str, Any]:
    with _maybe_chdir(request.working_dir):
        config = build_agent_config(request)
        log = AgentLogCollector()
        executor = TaskExecutor(log)
        result = await executor.execute_for_titles(
            paper_groups=_normalize_paper_groups(request.papers),
            config=config,
            output_prefix=request.output_prefix,
        )

        if result is None:
            result = {"status": "unknown", "outputs": {}}

        result.setdefault("status", "success")
        result["logs"] = log.get_recent_logs(request.logs_tail)
        result["events"] = log.events[-request.logs_tail:]
        result["progress"] = log.current_progress
        return result


def run_agent_request(request: AgentRunRequest) -> dict[str, Any]:
    return asyncio.run(run_agent_request_async(request))


def list_result_folders(data_dir: str | Path = "data") -> list[dict[str, Any]]:
    root = Path(data_dir)
    folders = []
    if not root.exists():
        return folders
    for item in root.iterdir():
        if not item.is_dir() or not item.name.startswith("result-"):
            continue
        files = [path for path in item.iterdir() if path.is_file()]
        folders.append({
            "name": item.name,
            "path": str(item),
            "file_count": len(files),
            "size": sum(path.stat().st_size for path in files),
            "modified": max((path.stat().st_mtime for path in files), default=item.stat().st_mtime),
        })
    folders.sort(key=lambda entry: entry["modified"], reverse=True)
    return folders


def read_result_summary(result_dir: str | Path) -> dict[str, Any]:
    root = Path(result_dir)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Result directory not found: {root}")

    json_files = sorted(root.glob("*_results.json"))
    excel_files = sorted(root.glob("*_results*.xlsx"))
    dashboard_files = sorted(root.glob("*_dashboard.html"))

    record_count = None
    if json_files:
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        if isinstance(data, list):
            record_count = len(data)
        elif isinstance(data, dict):
            record_count = len(data)

    return {
        "result_dir": str(root),
        "record_count": record_count,
        "files": {
            "json": str(json_files[0]) if json_files else None,
            "excel": str(excel_files[0]) if excel_files else None,
            "dashboard": str(dashboard_files[0]) if dashboard_files else None,
        },
    }
