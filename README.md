# browser-to-mcp

Turn reviewed browser workflows into typed, reliable MCP tools.

This repository contains the implementation of the proposal at
https://browser-to-mcp.github.io/spec/.

Current product scope and implementation sequencing are documented in
[`docs/PRODUCT_SPEC.md`](docs/PRODUCT_SPEC.md) and
[`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md). Historical research remains in the
[research repository](https://github.com/browser-to-mcp/research).

## Current milestone

M3 policy, native WebMCP discovery, regression, and readiness coverage are
complete. The local workflow-backed MCP server and protocol coverage are
implemented.
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
uv run browser-to-mcp replay workflows/hn_search.yaml --input query=python
uv run python examples/mcp_demo.py --query python
uv run browser-to-mcp discover https://webmcp.sh/ \
  --browser-arg=--enable-features=WebMCPTesting
uv build
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

The checked-in [`examples/mcp_demo.py`](examples/mcp_demo.py) starts that STDIO
server as a child process, lists the generated `hn_search` schema, calls the
tool, and prints its structured result and trace path. Run it when you want to
see the complete MCP request instead of leaving the server waiting for a
client. The live Hacker News result set changes over time; the protocol tests
use a deterministic fixture for repeatable schema and invocation checks.

`discover` opens a page and records native WebMCP tool metadata, including names,
descriptions, input schemas, origins, annotations, and the page's `tools`
Permissions Policy state. Discovery never invokes a native tool. It writes a
content-addressed JSON snapshot under `traces/`, and reports unsupported browsers,
permission blocking, empty tool lists, and malformed metadata as structured
diagnostics. Current Chromium testing builds expose the API behind the
`WebMCPTesting` feature, which can be passed with repeated `--browser-arg`
options. Use the `--browser-arg=VALUE` form when the browser argument itself
starts with a dash; use `--browser-channel` when the API is available in a
separately installed Playwright browser channel. Native page-provided tools are an
interoperability boundary; they are separate from reviewed workflow-backed MCP
tools and are not converted or executed by this project.

## V0 scope

This private pre-release is a local, read-only demonstration. It requires no
credentials or persistent browser state and does not claim broad website
coverage. It does not support writes, destructive actions, payments, uploads,
messaging, authentication-sensitive workflows, hosted browsers, or remote
transports. Repository access remains restricted to collaborators until a
separate public-release decision completes the M3 readiness gate.

See [`docs/DEMO.md`](docs/DEMO.md) for clean-checkout setup, MCP client
configuration, and successful/failed trace walkthroughs, [`CONTRIBUTING.md`](CONTRIBUTING.md) for development, and
[`SECURITY.md`](SECURITY.md) for security reports. Release preparation is
described in [`docs/RELEASE.md`](docs/RELEASE.md). The M3 fixture matrix and
its reproducible measurements are in [`docs/READINESS.md`](docs/READINESS.md).
