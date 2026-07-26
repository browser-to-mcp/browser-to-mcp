# Reproducible V0 demo

This walkthrough exercises the complete local path from a reviewed workflow to
a typed MCP call. It uses the public Hacker News search page and does not need
credentials or local browser state.

## Clean checkout

From a fresh checkout:

```bash
uv sync --all-groups
uv run playwright install chromium
```

Validate the reviewed workflow without opening a browser:

```bash
uv run browser-to-mcp validate workflows/hn_search.yaml
```

Inspect the planned actions:

```bash
uv run browser-to-mcp dry-run workflows/hn_search.yaml
```

Run the browser workflow directly:

```bash
uv run browser-to-mcp replay workflows/hn_search.yaml \
  --input query=python
```

The command returns the visible Hacker News text, completed action records, a
manifest digest, and a redacted JSON trace under `traces/`. The live result
count and story list are expected to change.

## MCP invocation

Run the checked-in client demonstration:

```bash
uv run python examples/mcp_demo.py \
  --query python \
  --trace-dir traces
```

The demonstration starts the local server over STDIO, performs `tools/list`,
and then calls `hn_search` with the typed input `query=python`. Its JSON output
contains:

- the generated input schema;
- the generated output schema;
- the workflow name and manifest digest;
- the extracted results;
- the trace path and duration.

The protocol contract is also checked without the live website:

```bash
uv run pytest tests/test_mcp_protocol.py
```

Those tests use a controlled fixture so schema and transport assertions remain
repeatable in CI.

## Failure and safety behavior

Try an invalid input before a browser starts:

```bash
uv run browser-to-mcp replay workflows/hn_search.yaml --input query=
```

The command returns a structured input failure. Workflow validation also
rejects non-read-only risk, undeclared domains, unsafe capabilities, missing
step declarations, and invalid success assertions.

The reference workflow waits for rendered result text before extraction. This
is important because the page loads search results asynchronously.
