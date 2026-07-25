# browser-to-mcp

Turn reviewed browser workflows into typed, reliable MCP tools.

This private repository contains the implementation of the proposal at
https://browser-to-mcp.github.io/spec/.

Current product scope and implementation sequencing are documented in
[`docs/PRODUCT_SPEC.md`](docs/PRODUCT_SPEC.md) and
[`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md). Historical research remains in the
[research repository](https://github.com/browser-to-mcp/research).

## Current milestone

M1 adds deterministic replay for the current read-only reference workflow.
Browser workflow coverage and MCP serving are tracked as later milestones.
Every step must explicitly declare its timeout, risk, idempotency, retry, and
confirmation policy. V0 accepts only read-only workflows and refuses writes,
destructive or financial actions, uploads, messaging, and authentication-
sensitive workflows before a browser starts.

## Development

```bash
uv sync --all-groups
uv run browser-to-mcp validate workflows/hn_search.yaml
uv run browser-to-mcp dry-run workflows/hn_search.yaml
uv run playwright install chromium
uv run browser-to-mcp replay workflows/hn_search.yaml --input query=mcp
uv run pytest
```

`replay` supports the read-only `goto`, `fill`, `press`, `click`,
`wait_for_text`, `extract_text`, and permission-gated `download` steps. It runs
in a fresh headless Chromium context, limits every action and the complete run,
and blocks navigation or page requests outside `permissions.allowed_domains`.
Use `--timeout-seconds` to set the overall run limit. It returns structured
JSON, including one result for each completed action. Traces and MCP serving
are tracked separately. Each replay writes a redacted JSON trace under
`traces/`; use `--trace-dir` to choose another directory. Failed runs also
attempt to save a failure screenshot and include its path in the trace.
