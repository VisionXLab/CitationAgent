from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from citationclaw.agent import (
    AgentRunRequest,
    list_result_folders,
    read_result_summary,
    run_agent_request,
    validate_agent_config,
)


def _load_json(path: str | None) -> dict[str, Any]:
    if not path or path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(data: dict[str, Any] | list[dict[str, Any]], pretty: bool):
    json.dump(
        data,
        sys.stdout,
        ensure_ascii=False,
        indent=2 if pretty else None,
    )
    sys.stdout.write("\n")


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="citationclaw-agent")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_pretty(subparser: argparse.ArgumentParser):
        subparser.add_argument(
            "--pretty",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Pretty-print JSON output",
        )

    run_p = sub.add_parser("run", help="Run a paper-title analysis from a JSON request")
    run_p.add_argument("--request", required=True, help="Request JSON path, or '-' for stdin")
    add_pretty(run_p)

    validate_p = sub.add_parser("validate-config", help="Validate agent config and env")
    validate_p.add_argument("--request", help="Optional request JSON path, or '-' for stdin")
    add_pretty(validate_p)

    list_p = sub.add_parser("list-results", help="List result folders")
    list_p.add_argument("--data-dir", default="data")
    add_pretty(list_p)

    summary_p = sub.add_parser("summary", help="Read a result directory summary")
    summary_p.add_argument("result_dir")
    add_pretty(summary_p)

    args = parser.parse_args(argv)

    if args.command == "run":
        request = AgentRunRequest(**_load_json(args.request))
        _emit(run_agent_request(request), args.pretty)
    elif args.command == "validate-config":
        raw = _load_json(args.request) if args.request else {"papers": [{"title": "placeholder"}]}
        request = AgentRunRequest(**raw)
        _emit(validate_agent_config(request), args.pretty)
    elif args.command == "list-results":
        _emit(list_result_folders(args.data_dir), args.pretty)
    elif args.command == "summary":
        _emit(read_result_summary(args.result_dir), args.pretty)


if __name__ == "__main__":
    main()
