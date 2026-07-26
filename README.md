# browser-to-mcp

Turn reviewed browser workflows into typed, reliable MCP tools.

This private repository contains the implementation of the proposal at
https://browser-to-mcp.github.io/spec/.

Current product scope and implementation sequencing are documented in
[`docs/PRODUCT_SPEC.md`](docs/PRODUCT_SPEC.md) and
[`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md). Historical research remains in the
[research repository](https://github.com/browser-to-mcp/research).

## Current milestone

M3 adds policy, native WebMCP discovery, regression, and readiness coverage.
The local workflow-backed MCP server and protocol coverage are implemented.
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
uv run browser-to-mcp server --workflow-dir workflows
uv run browser-to-mcp discover https://webmcp.sh/ \
  --browser-arg=--enable-features=WebMCPTesting
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

`server` exposes every valid YAML workflow in the selected directory over a
local STDIO MCP connection. Startup validates all workflows before registering
any tools, and invalid or unsafe files prevent startup. Tool calls validate
typed inputs before opening a browser, then return workflow outputs, the
manifest digest, and the trace path. The server exposes workflow-backed tools;
it does not discover or invoke native page-provided tools.

`discover` opens a page and records native WebMCP tool metadata, including names,
descriptions, input schemas, origins, annotations, and the page's `tools`
Permissions Policy state. Discovery never invokes a native tool. It writes a
content-addressed JSON snapshot under `traces/`, and reports unsupported browsers,
permission blocking, empty tool lists, and malformed metadata as structured
diagnostics. Current Chromium testing builds expose the API behind the
`WebMCPTesting` feature, which can be passed with repeated `--browser-arg`
options; use `--browser-channel` when the API is available in a separately
installed Playwright browser channel. Native page-provided tools are an
interoperability boundary; they are separate from reviewed workflow-backed MCP
tools and are not converted or executed by this project.
