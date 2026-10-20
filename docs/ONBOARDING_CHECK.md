# Check the first-run experience

## Current evidence

The project owner reported on 2026-10-21 that they had checked the experience
and that it looked good so far. They did not provide a measured duration,
installation time, or whether the tester was unfamiliar with the code.
That feedback is useful, but it does not establish the under-ten-minute target.

Automated results are **observed by automated software using real Chromium and
a real MCP server**. The software uses actual recordings, page clicks, saved
workflows, and MCP calls. Scripted answers guide the terminal prompts. The
[verification guide](JOURNEY_CHECKS.md) explains how to reproduce the results and
where to find the measured report. The automated time to the first correct result
is not a measurement of how quickly a new person can follow the guide.

Status: **independent newcomer timing is not measured**. Issues #48 and #41 must
remain open until that requirement has evidence, or the project owner explicitly
changes the acceptance criteria. No successful human walkthrough is claimed.

## A walkthrough without an external website

Use a clean checkout. Installation commands are in [DEMO.md](DEMO.md). Measure
installation time separately. With dependencies and Chromium installed, start
this local catalog in a terminal and keep it running:

```bash
uv run python tests/authoring/catalog.py --port 8765
```

It binds only to `127.0.0.1` and serves original example pages. It requires no
account and makes no third-party requests. Stop it with Ctrl+C when finished.
If the port is busy, choose another port and use that same port in the URL below.

In a second terminal, start the task timer and run:

```bash
mkdir -p recordings pilot-workflows
uv run browser-to-mcp record \
  http://127.0.0.1:8765/catalogue/a-light-in-the-attic_1000/index.html \
  --allow-domain 127.0.0.1 --workflow-dir pilot-workflows \
  --draft recordings/local-book.recording.json
```

Once the book appears, type `stop` in the terminal. Then run:

```bash
uv run browser-to-mcp author recordings/local-book.recording.json \
  --output pilot-workflows/local-book.yaml
```

Follow the numbered choices in [AUTHORING.md](AUTHORING.md). Use tool name
`catalog_product_lookup`, input `product_slug`, and binding `0/path:2`. Select
the title and price **inside the main product area**, not the deliberately wrong
book shown elsewhere on the page. Use string outputs named `title` and `price`.
Review the whole plan before typing `save`.

The destination directory must contain just this tool for the walkthrough. If
you have already saved another workflow with the same tool name, choose a fresh
directory; the server correctly refuses duplicate names. Existing recordings
and workflows are never silently overwritten.

Validate the saved file and run it directly with the second book:

```bash
uv run browser-to-mcp validate pilot-workflows/local-book.yaml
uv run browser-to-mcp replay pilot-workflows/local-book.yaml \
  --input product_slug=shakespeares-sonnets_989 --trace-dir traces
```

Call the same tool over a real local MCP connection using the included client:

```bash
uv run python examples/mcp_demo.py --workflow-dir pilot-workflows \
  --tool-name catalog_product_lookup \
  --input product_slug=shakespeares-sonnets_989 --trace-dir traces
```

This client starts and stops its own server. Stop the task timer when its `call`
result contains `title: "Shakespeare's Sonnets"` and `price: "£20.66"`. The result
also includes the workflow name, manifest digest, and trace path. Use the client
configuration in [DEMO.md](DEMO.md) to connect another MCP client; restart its
server after adding a workflow.

Inspect success and failure records:

```bash
python -m json.tool traces/<run-id>.json
uv run browser-to-mcp replay pilot-workflows/local-book.yaml \
  --input product_slug=browser-to-mcp-missing_0 --trace-dir traces
```

The missing book must fail at `steps.title` with category `runtime` and a failed
trace. An empty `product_slug` fails input validation before opening a browser.
A missing MCP argument is rejected by the MCP schema before replay and therefore
has no replay trace. Public browser failures can also save a screenshot locally.

## Record the human result when available

- Date, operating system, Python version, and commit tested.
- Whether the contributor had used this code before.
- Installation time and any installation problems, or “not measured”.
- Time from installed prerequisites to the first correct MCP result.
- Exact input and returned title and price.
- Any help, confusing prompt, selector edit, retry, or failed run.
- Whether the task took less than ten minutes.
- If it did not, the specific obstacle and a linked follow-up ticket.

Do not include personal details, credentials, or private browser state. Keep
live-site observations separate from this controlled catalog. The dated live
recording/authoring results are in
[the 2026-10-20 evidence](evidence/authoring-2026-10-20.json); they are not a
newcomer timing exercise or a promise about other websites.
