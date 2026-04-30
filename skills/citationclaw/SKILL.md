---
name: citationclaw
description: Use CitationClaw as an agent-facing citation-impact analysis tool through its headless CLI or MCP server.
---

# CitationClaw Agent Skill

Use this skill when a user asks an agent to analyze how a paper is cited, generate a citation portrait, inspect CitationClaw results, or run CitationClaw without manually using the Web UI.

## Entry Points

- Validate config: `citationclaw-agent validate-config --request request.json --pretty`
- Run headless: `citationclaw-agent run --request request.json --pretty`
- List results: `citationclaw-agent list-results --data-dir data --pretty`
- Summarize one result: `citationclaw-agent summary data/result-YYYYmmdd_HHMMSS --pretty`
- MCP server: `python3 -m citationclaw.mcp_server` (or `citationclaw-mcp` when console scripts are on `PATH`)

## Request Shape

```json
{
  "papers": [
    {
      "title": "Target paper title",
      "aliases": ["Earlier or alternate title"]
    }
  ],
  "output_prefix": "paper",
  "service_tier": "basic",
  "config_overrides": {
    "enable_dashboard": true
  }
}
```

For offline smoke tests, set:

```json
{
  "config_overrides": {
    "test_mode": true,
    "enable_citing_description": false,
    "enable_dashboard": false
  }
}
```

## Environment

Prefer environment variables for secrets:

- `CITATIONCLAW_SCRAPER_API_KEYS` or `SCRAPERAPI_KEY`
- `CITATIONCLAW_OPENAI_API_KEY` or `OPENAI_API_KEY`
- `CITATIONCLAW_OPENAI_BASE_URL` or `OPENAI_BASE_URL`
- `CITATIONCLAW_OPENAI_MODEL` or `OPENAI_MODEL`

Do not write API keys into shared request files or commit them.

## Agent Rules

- Run `validate-config` before real crawling.
- Use `test_mode` for CI, demos, and MCP smoke checks.
- Treat generated Excel, JSON, and HTML files under `data/result-*` as the durable artifacts.
- Respect CitationClaw's `CC BY-NC 4.0` license and the terms of upstream data/API providers.
