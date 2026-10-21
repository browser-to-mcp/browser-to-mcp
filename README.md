# browser-to-mcp

Turn reviewed browser workflows into typed, read-only MCP tools.

Record a public browser task, review it with `author`, or write a workflow in YAML.
The project validates the permissions,
runs the workflow in Chromium, and exposes each valid workflow as an MCP tool.

## Quick start

```bash
uv sync --all-groups
uv run playwright install chromium
```

Run a workflow directly:

```bash
uv run browser-to-mcp replay workflows/hn_search.yaml --input query=python
uv run browser-to-mcp replay workflows/wikipedia_search.yaml --input query=python
```

Each run returns structured JSON with the workflow result, actions, duration,
manifest digest, and trace path.

## Record a demonstration

Capture a public, read-only task in a fresh visible browser:

```bash
mkdir -p recordings
uv run browser-to-mcp record https://books.toscrape.com/ \
  --allow-domain books.toscrape.com --allow-domain ajax.googleapis.com \
  --draft recordings/book-lookup.recording.json
```

Type `stop` in the terminal to save, or `cancel` to discard. An incomplete
capture requires an explicit `save-partial` choice. The result is an unreviewed
draft, not an MCP tool. See [the recording guide](docs/RECORDING.md) for supported
actions, privacy, and browser boundaries.

## Turn the recording into a tool

```bash
uv run browser-to-mcp author recordings/book-lookup.recording.json --output workflows/book.yaml
```

Choose reusable inputs, click page text to select outputs, and review the full
plan before saving. Follow the [book walkthrough](docs/AUTHORING.md) for exact
steps. Restart the MCP server after export so it can load the new tool.

## Check the complete journey

Follow the [local walkthrough](docs/ONBOARDING_CHECK.md) to record a book lookup
and call it through MCP without relying on a public website. The
[verification guide](docs/JOURNEY_CHECKS.md) explains the real-browser tests,
thirty measured MCP runs, saved evidence, and remaining newcomer timing check.

## Available workflows

| Workflow | What it does |
| --- | --- |
| `hn_search` | Searches Hacker News and returns visible results. |
| `wikipedia_search` | Searches Wikipedia and returns visible article or search text. |

## Use as an MCP server

Start the local STDIO server:

```bash
uv run browser-to-mcp server --workflow-dir workflows
```

The server normally prints nothing after startup. It waits for an MCP client
to connect and call one of the generated tools. See
[`docs/DEMO.md`](docs/DEMO.md) for client configuration and a complete demo.

You can also run the included client example:

```bash
uv run python examples/mcp_demo.py --query python
```

## How a workflow works

A workflow is a YAML recipe containing:

- typed inputs, such as `query`;
- allowed domains and safety permissions;
- browser actions, such as `goto`, `click`, `wait_for_text`, and `extract_text`;
- named outputs and a success check.

Every action declares its timeout, risk, retry, idempotency, and confirmation
policy. V0 requires `retry: none` and attempts each step at most once. The
runner blocks navigation outside the declared domains and writes a redacted
trace for every run.

## WebMCP discovery

The separate `discover` command inspects native tools exposed by a webpage. It
does not call those tools or convert them into workflow-backed MCP tools.

```bash
uv run browser-to-mcp discover https://webmcp.sh/ \
  --browser-arg=--enable-features=WebMCPTesting
```

## Current limitations

This is a private, local, read-only pre-release. It currently does not provide:

- hosted browsers or remote MCP transport;
- login, persistent browser sessions, or credential storage;
- writes, destructive actions, payments, uploads, or messaging;
- automatic handling or bypassing of browser challenges;
- broad coverage of arbitrary websites.

The project is focused on reliable, permissioned browser workflows first.

## Documentation

- [`docs/DEMO.md`](docs/DEMO.md) — clean setup, MCP clients, and trace walkthroughs
- [`docs/PRODUCT_SPEC.md`](docs/PRODUCT_SPEC.md) — product behavior and boundaries
- [`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) — safety declarations, retry policy, and URL input rules
- [`docs/OUTPUTS.md`](docs/OUTPUTS.md) — named fields, result types, parsing, and failure behavior
- [`docs/RECORDING.md`](docs/RECORDING.md) — browser recording, draft format, and safe save/cancel flow
- [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) — implementation roadmap
- [`docs/PILOT.md`](docs/PILOT.md) — selected recorder task, examples, and test-page requirements
- [`docs/READINESS.md`](docs/READINESS.md) — readiness checks and measurements
- [`docs/RELEASE.md`](docs/RELEASE.md) — release preparation
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development workflow
- [`SECURITY.md`](SECURITY.md) — security reports

The implementation follows the [browser-to-mcp specification](https://browser-to-mcp.github.io/spec/).
