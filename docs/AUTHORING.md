# Turn a recording into a tool

The `author` command helps you choose inputs, select the text to return, and
review the complete workflow. It saves YAML that the existing replay engine and
MCP server can use. You do not need to write selectors for the book example.

## Record the example

After installing the dependencies and Chromium as described in [DEMO.md](DEMO.md):

```bash
mkdir -p recordings pilot-workflows
uv run browser-to-mcp record \
  https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html \
  --allow-domain books.toscrape.com --allow-domain ajax.googleapis.com \
  --workflow-dir pilot-workflows --draft recordings/book.recording.json
```

Wait for the book page, then type `stop` in the terminal. This public demo site
uses made-up prices. No login is needed. See [RECORDING.md](RECORDING.md) for
supported actions and privacy limits. Partial drafts cannot be made into tools;
record a complete demonstration first.

## Choose inputs and outputs

```bash
uv run browser-to-mcp author recordings/book.recording.json \
  --output pilot-workflows/book.yaml
```

1. Read each recorded event. Confirm it only reads public information and is
   safe to repeat. Refuse actions you cannot explain. For a later navigation,
   say whether a previous click caused it or you entered the URL yourself.
   A navigation caused by another action is an observation, not a second `goto`.
2. Review the domains. They include subdomains under the existing runtime policy.
   No supporting domain is added automatically. Agree to replay the demonstration
   in a fresh visible browser.
3. Name the tool `catalog_product_lookup` and describe it as `Look up a book`.
4. Add a required string input named `product_slug`, with description `Book slug`
   and minimum length `1`. Choose `0/path:2`, the recorded book slug. Choose no
   when asked to add another input. Other occurrences of that text stay fixed.
   No demonstration value becomes a default.
5. Choose no for a readiness wait on this example. For other pages you can add
   fixed visible text to wait for, with a ten-second limit. Extraction waits up
   to five seconds for its target; navigation waits up to thirty seconds.
6. Click the book heading in the browser, then press Enter in the terminal.
   Review the selector and preview. Name the output `title`, choose `string`,
   and enter a short description. Add another output by clicking the price.
   Name it `price`, also a `string`. The pound sign is part of the text.
7. Read the entire YAML shown in the terminal. Check the steps, input bindings,
   outputs, success checks, domains, timeouts, and safety declarations.
   Type `save` only when this plan is correct.

Type `cancel` at any prompt to discard the plan. EOF and Ctrl+C also cancel.
An existing destination is never overwritten. Create its parent directory first.
The final file is checked by the normal loader, policy checks, and schema compiler
before it appears in the workflow directory.

Selection clicks do not activate links or buttons. Selection uses unique
attributes or a short CSS path made from tags and classes; it never guesses an
element by its position. Missing, hidden, or ambiguous targets must be selected
again. Text inside forms, frames, shadow roots, and editable controls is unsupported.
A site without a stable unique target may need its markup changed before it can
be used. Review cannot guarantee that an arbitrary website is free of side effects.

Inputs and outputs support string, integer, number, and boolean types. Preview
conversion follows [OUTPUTS.md](OUTPUTS.md), including finite numbers and valid
`0` and `false` results. String inputs can have a positive minimum length.
Optional inputs can be declared, but cannot be bound to a step: V0 has no
conditional steps or fallback values. The pilot uses one required input.
URL bindings affect one complete path segment or one query value. The scheme,
host, parameter names, and other URL text remain fixed.

## Test and connect

```bash
uv run browser-to-mcp validate pilot-workflows/book.yaml
uv run browser-to-mcp replay pilot-workflows/book.yaml \
  --input product_slug=shakespeares-sonnets_989 --trace-dir traces
```

The expected fields are `title: "Shakespeare's Sonnets"` and `price: "£20.66"`.
Inspect the returned `trace_path` using `python -m json.tool PATH`.
Try `--input product_slug=browser-to-mcp-missing_0` for a deliberate missing-book
failure at `steps.title`. An empty value fails input validation before a browser
starts. External site changes or outages can also fail a run; inspect the trace.

Use the client configuration in [DEMO.md](DEMO.md), changing its workflow directory
to `pilot-workflows`. Restart the client's server process after saving. The server
reads workflows only at startup; there is no hot reload. It will list
`catalog_product_lookup`, accepting `{"product_slug":"shakespeares-sonnets_989"}`.
The returned result includes named outputs, workflow identity, digest, and trace path.

## Repeatable checks

```bash
uv run pytest tests/authoring -q
```

These tests use a local catalog, actual Chromium capture and selection, exported
YAML, and an actual STDIO MCP process. No live website or private account is needed.
They include ten runs of each of the three pilot cases, delayed content, special
characters, cancellation, and broken targets. This proves the controlled example;
it does not establish reliability across arbitrary websites.

The independent new-contributor timing exercise is still pending. Use
[ONBOARDING_CHECK.md](ONBOARDING_CHECK.md) to record it honestly before closing
issues #48 and #41.

For an explicit live-site smoke run of your exported book workflow:

```bash
uv run python examples/pilot_smoke.py pilot-workflows/book.yaml
```

It reports three attempts, exact expected outcomes, failures, durations, and trace
paths as JSON. Keep those observations separate from CI results. This script does
not create or review a workflow for you.

The [2026-10-20 live observations](evidence/authoring-2026-10-20.json) contain both
checks: the first selector implementation matched 1 of 3 expected outcomes;
after choosing targets within their surrounding product section, the follow-up
matched all 3. Each check used one attempt per input. Prompt answers were scripted,
but capture, page clicks, authoring, and replay used actual Chromium and the normal
code paths. This was not an independent newcomer timing exercise.
