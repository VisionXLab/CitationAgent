from __future__ import annotations

from typing import Any

from citationclaw.agent import (
    AgentRunRequest,
    list_result_folders,
    read_result_summary,
    run_agent_request_async,
    validate_agent_config,
)


def _load_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit(
            "citationclaw-mcp requires the MCP Python SDK. "
            "Install with: pip install 'citationclaw[agent]'"
        ) from exc
    return FastMCP


FastMCP = _load_fastmcp()
mcp = FastMCP("CitationClaw")


@mcp.tool()
def validate_config(request: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = request or {"papers": [{"title": "placeholder"}]}
    return validate_agent_config(AgentRunRequest(**raw))


@mcp.tool()
async def run_titles(request: dict[str, Any]) -> dict[str, Any]:
    return await run_agent_request_async(AgentRunRequest(**request))


@mcp.tool()
def list_results(data_dir: str = "data") -> list[dict[str, Any]]:
    return list_result_folders(data_dir)


@mcp.tool()
def read_result(result_dir: str) -> dict[str, Any]:
    return read_result_summary(result_dir)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
