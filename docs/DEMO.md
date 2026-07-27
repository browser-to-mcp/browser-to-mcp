# Local onboarding and trace walkthrough

This guide takes a new contributor from a clean checkout to a working local MCP
tool. V0 is a local, read-only demonstration: it does not support credentials,
writes, hosted browsers, remote transports, or broad website coverage.

## Requirements

- Python 3.11 or later;
- [uv](https://docs.astral.sh/uv/);
- a clean checkout of this repository.

## Set up a clean checkout

From the repository root, install a supported Python, the locked dependencies,
and Chromium used by Playwright:

```bash
uv python install 3.11
uv sync --locked --all-groups
uv run playwright install chromium
```

The project accepts Python 3.11 and later. `uv sync --locked` ensures the
checked-in dependency versions are used.

## Validate and inspect the reference workflow

The reference workflow is [`workflows/hn_search.yaml`](../workflows/hn_search.yaml).
Validate it without opening a browser:

```bash
uv run browser-to-mcp validate workflows/hn_search.yaml
```

Expected result: JSON with `"status": "valid"`, the workflow name, and a
`manifest_digest`. The digest identifies this exact reviewed workflow contract.

Inspect the reviewed action plan:

```bash
uv run browser-to-mcp dry-run workflows/hn_search.yaml
```

Expected result: JSON containing the allowed domains, typed input, and the
`search`, `wait_for_results`, and `results` steps. `dry-run` never opens a
browser.

## Run the workflow and inspect a successful trace

Run the browser workflow with a query:

```bash
uv run browser-to-mcp replay workflows/hn_search.yaml \
  --input query=python \
  --trace-dir traces
```

Expected result: JSON with `"status": "completed"`, visible Hacker News
results, completed action records, a manifest digest, and a `trace_path`. Live
story results can change, but the action order remains the same.

Copy the returned `trace_path` and inspect it with Python's built-in JSON
formatter:

```bash
python -m json.tool traces/<run-id>.json
```

A successful trace contains:

- `metadata`: workflow name/version, manifest digest, runtime versions, and
  duration;
- `actions`: each declared step and its outcome;
- `outputs`: extracted values;
- `assertions`: post-run success checks;
- `status: "completed"`.

## Inspect a controlled failure trace

Run the same workflow with an invalid empty input:

```bash
uv run browser-to-mcp replay workflows/hn_search.yaml \
  --input query= \
  --trace-dir traces
```

This command intentionally exits with code `2`. Expected error JSON includes
`"status": "failed"`, `"category": "input"`, `"path": "inputs.query"`, and
a `trace_path`. No browser starts because the input is rejected first.

Inspect that trace using the same command:

```bash
python -m json.tool traces/<run-id>.json
```

A failed trace records `status: "failed"` plus `error.category`, `error.path`,
and `error.message`. Browser failures also attempt to save a screenshot and
record its path. Inputs marked `sensitive: true` in a workflow are replaced by
`[REDACTED]` in trace inputs, outputs, and error messages; the reference
`query` input is intentionally not sensitive.

## Understand the workflow contract

Each workflow declares a tool name, policy, typed inputs/outputs, ordered
browser steps, and success assertions. Every step must declare its safety and
retry behavior. This is a minimal read-only shape:

```yaml
name: example_page_title
description: Read visible text from an approved page.
version: 1
risk: read_only

manifest:
  version: 1

permissions:
  allowed_domains:
    - example.com

inputs: {}

steps:
  - id: open_page
    action: goto
    url: https://example.com/
    timeout_seconds: 30
    risk: read_only
    idempotency: safe
    retry: none
    confirmation: never
  - id: page_text
    action: extract_text
    selector: main
    timeout_seconds: 30
    risk: read_only
    idempotency: safe
    retry: none
    confirmation: never

outputs:
  page_text:
    type: string

success:
  - id: page_text_present
    assertion: output_not_empty
    field: page_text
```

The runtime rejects undeclared domains, non-read-only actions, side-effect
capabilities, and incomplete safety declarations before browser execution.

## Connect an MCP client

The server uses local STDIO. It is not a web server and does not listen on a
network port. An MCP-compatible client starts it as a child process and sends
tool requests through standard input/output.

Use this configuration shape in a client that supports local STDIO servers.
Replace the `cwd` value with the absolute path to your checkout:

```json
{
  "mcpServers": {
    "browser-to-mcp": {
      "command": "uv",
      "args": [
        "run",
        "browser-to-mcp",
        "server",
        "--workflow-dir",
        "workflows"
      ],
      "cwd": "/absolute/path/to/browser-to-mcp"
    }
  }
}
```

After the client restarts, it discovers the generated `hn_search` tool. A call
such as `hn_search({"query": "python"})` launches a fresh Chromium browser,
runs the reviewed workflow, and returns results, the manifest digest, and a
trace path.

Do not run the server command separately when the client is configured; the
client owns that process. If you do run it manually, a quiet terminal is
expected because the server is waiting for an MCP request. Use `Ctrl+C` to stop
it.

```bash
uv run browser-to-mcp server --workflow-dir workflows
```

To verify the complete local protocol without configuring another client, run:

```bash
uv run python examples/mcp_demo.py --query python --trace-dir traces
```

The demonstration starts the local server, lists `hn_search`, calls it, and
prints its schema, structured result, and trace path.

## Related documents

- [Product specification](PRODUCT_SPEC.md): V0 scope and safety boundary.
- [Build plan](BUILD_PLAN.md): completed milestones and deferred work.
- [Readiness report](READINESS.md): repeatability fixture matrix and release gate.
- [Contribution guide](../CONTRIBUTING.md): development workflow.
- [Security policy](../SECURITY.md): responsible vulnerability reporting.
