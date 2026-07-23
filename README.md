# browser-to-mcp

Turn reviewed browser workflows into typed, reliable MCP tools.

This private repository contains the implementation of the proposal at
https://browser-to-mcp.github.io/spec/.

## Current milestone

M0 validates read-only YAML workflow contracts locally. Browser replay and MCP
serving are tracked as later milestones.

## Development

```bash
uv sync --all-groups
uv run browser-to-mcp validate workflows/hn_search.yaml
uv run pytest
```
