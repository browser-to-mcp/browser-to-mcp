# browser-to-mcp

Turn reviewed browser workflows into typed, reliable MCP tools.

This private repository contains the implementation of the proposal at
https://browser-to-mcp.github.io/spec/.

## Current milestone

M1 adds deterministic replay for the current read-only reference workflow.
Browser workflow coverage and MCP serving are tracked as later milestones.

## Development

```bash
uv sync --all-groups
uv run browser-to-mcp validate workflows/hn_search.yaml
uv run browser-to-mcp dry-run workflows/hn_search.yaml
uv run playwright install chromium
uv run browser-to-mcp replay workflows/hn_search.yaml --input query=mcp
uv run pytest
```

`replay` currently supports the `goto` and `extract_text` steps used by the
reference workflow. It runs in a fresh headless Chromium context and returns
structured JSON. More actions, traces, and MCP serving are tracked separately.
