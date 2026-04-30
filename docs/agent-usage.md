# CitationClaw Agent Usage

CitationClaw can be used by agents through a small headless adapter in addition to the existing Web UI.

## Install

```bash
pip install citationclaw
pip install "citationclaw[agent]"  # optional: MCP server support
```

For local development:

```bash
pip install -e ".[dev,agent]"
```

## Configure Secrets

Prefer environment variables:

```bash
export CITATIONCLAW_SCRAPER_API_KEYS="scraper-key-1,scraper-key-2"
export CITATIONCLAW_OPENAI_API_KEY="llm-key"
export CITATIONCLAW_OPENAI_BASE_URL="https://api.example.com/v1"
export CITATIONCLAW_OPENAI_MODEL="search-capable-model"
```

The agent adapter reads these values at runtime and does not persist them to `config.json`.

## Run From CLI

Create `request.json`:

```json
{
  "papers": [
    {
      "title": "Attention Is All You Need",
      "aliases": []
    }
  ],
  "output_prefix": "attention",
  "service_tier": "basic"
}
```

Validate:

```bash
citationclaw-agent validate-config --request request.json --pretty
```

Run:

```bash
citationclaw-agent run --request request.json --pretty
```

If console scripts are not on `PATH`, use `python3 -m citationclaw.agent_cli` with the same subcommands.

The command returns a JSON envelope with `status`, `result_dir`, `outputs`, `cost_summary`, recent `logs`, and progress/events.

## Offline Smoke Test

Use `test_mode` to avoid ScraperAPI and LLM calls:

```json
{
  "papers": [{"title": "Smoke Test Paper"}],
  "output_prefix": "smoke",
  "config_overrides": {
    "test_mode": true,
    "enable_citing_description": false,
    "enable_dashboard": false
  }
}
```

## MCP

After installing `citationclaw[agent]`, expose the server with:

```bash
python3 -m citationclaw.mcp_server
```

The server exposes:

- `validate_config`
- `run_titles`
- `list_results`
- `read_result`

The repository also includes `.mcp.json` for clients that can discover MCP server definitions from the project root.
