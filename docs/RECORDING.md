# Record a public browser task

Recording, preview, and replay share the [browser restrictions](BROWSER_POLICY.md).
Only GET, HEAD, and OPTIONS are allowed; HTTP redirects and unsupported browser
capabilities fail the session. A method or `read_only` label cannot guarantee
that a website action has no side effects. Review the linked migration steps.


The recorder lets you demonstrate a small task in a fresh, visible Chromium
window. It saves a local draft containing supported actions and navigation
observations. It never replays the captured actions during recording.

Use public, non-sensitive, read-only tasks. Your clicks and keys act on the
live website. Recording cannot detect every possible side effect, and a GET
request can also change a website. Do not log in, buy, submit private data,
or use controls that change an account.

## Start, finish, or cancel

Install the project and Chromium as described in the [README](../README.md).
Then create a directory outside your MCP server's workflow directory:

```bash
mkdir -p recordings
uv run browser-to-mcp record https://books.toscrape.com/ \
  --allow-domain books.toscrape.com \
  --allow-domain ajax.googleapis.com \
  --draft recordings/book-lookup.recording.json
```

A fresh window opens without imported cookies, a saved profile, or granted
permissions. Follow a book link to demonstrate the [pilot](PILOT.md). Avoid
basket controls. Return to the terminal and type one command, then Enter:

| Command | Result |
| --- | --- |
| `stop` | Close the browser and save a completed capture if it has no diagnostics. |
| `save-partial` | Explicitly save an incomplete draft for review, then close the browser. |
| `cancel` | Close the browser and discard the recording. |
| Ctrl+C or end of terminal input | Discard the recording and close resources. |

A browser close, capture error, password field, or unsupported interaction
makes the recording incomplete. `stop` then asks you to type `save-partial`
or `cancel`. An incomplete draft is never saved without that explicit choice.
If saving fails, the command returns a safe error and keeps no partial file.
Choose a new path and record again; there is no hidden recovery copy.

`--allow-domain` is required and repeatable. As in replay, a declared hostname
also permits its subdomains. Schemes, ports, wildcards, and paths are not
allowed in these host arguments. The starting URL must be HTTP(S) on an
allowed host. Dependencies outside that boundary stay blocked. Diagnostics
identify their hostname without saving the request path, query, or body;
review these before starting a new recording with additional domains.

If your server scans another directory, pass `--workflow-dir /path/to/workflows`.
The recorder rejects destinations inside that resolved directory, including
symlink aliases. It requires a new `.recording.json` filename and an existing
parent directory. Files are created with owner-only permissions and published
without replacing any existing file, even if another process creates it
while recording. `recordings/` is ignored by Git.

The CLI prints progress and diagnostics to stderr, and one JSON result to
stdout. Exit codes are 0 for an explicitly saved draft (including a partial
one), 1 for cancellation, 2 for configuration/save errors, and 130 for Ctrl+C.
A `captured` result still requires review; it is not a runnable workflow.

## What is captured

- Top-level navigation observations, including address changes and link
  destinations, in their observed order.
- Ordinary text fills. Consecutive typing in the same field becomes one
  final value. A blur, another action, or navigation ends that typing group.
- Primary clicks, preserving intentional repeated clicks.
- Supported keys such as Enter, Tab, Escape, arrows, Home/End, PageUp/PageDown,
  and Space, with modifiers and the target that received the key.

Text editing and paste are represented by the resulting fill. A keyboard
activation of a button is recorded once as a key press, not again as a click.
Replay now honors `press.selector` when present; existing key steps without
selectors still use the currently focused element.

Targets use unique test attributes, IDs, accessible labels, wrapped labels,
field names, placeholders, or button/link text. These are selectors accepted
by the existing Playwright replay path. The recorder refuses ambiguous
matches instead of inventing a positional selector. Selectors still need
review: a site may change its markup later.

Extra tabs are closed. Frames, shadow DOM, unsupported controls and shortcuts,
modified clicks, double-click gestures, drag/drop, touch, and wheel scrolling
produce diagnostics. Hover-only behavior is outside the supported action set.
Uploads/downloads, dialogs, WebSockets, and non-GET/HEAD/OPTIONS requests are
refused or dismissed. Service workers are blocked. Browser capabilities are
not automatically granted; common notification, location, media, and clipboard
requests are refused with diagnostics. This is a limited recorder, not a
sandbox for arbitrary or hostile website behavior.

## Privacy

The recorder saves only selected action fields, allowed domains, navigation
URLs, and safe diagnostics. It does not export cookies, storage, request or
response bodies, full DOM trees, browser exception details, or screenshots.

Password fields and recognizable credential fields stop capture before their
values are sent to Python. Common token values and credential-bearing URLs
are refused. Fragment URLs are also refused rather than retaining possible
credentials. These checks cannot recognize every secret pasted into an
ordinary text field or every secret encoded in a URL path. Demonstrate only
public data and inspect the draft before sharing it.

## Draft format and review

The typed format is `browser-to-mcp-recording`, version `1`, with
`status: captured|partial`, `requires_review: true`, `allowed_domains`, and an
ordered `events` array. Event kinds are `navigation`, `fill`, `click`, `press`,
and `diagnostic`. Interaction events have a `target` containing an executable
`selector` and the `strategy` used to choose it. Fill events include `value`;
key events include `key`. Diagnostics contain a fixed code/message and an
optional blocked hostname. No workflow permissions are inferred from capture.

Navigation events are observations, not an instruction to replay a click and
then navigate again. The authoring step must distinguish starting/manual
navigation from a destination reached by a preceding action. For example:

```json
{
  "format": "browser-to-mcp-recording",
  "version": 1,
  "status": "captured",
  "requires_review": true,
  "allowed_domains": ["books.toscrape.com"],
  "events": [
    {"kind": "navigation", "url": "https://books.toscrape.com/"},
    {
      "kind": "click",
      "target": {
        "selector": "a:has-text(\"A Light in the Attic\")",
        "strategy": "text"
      }
    }
  ]
}
```

The MCP server scans only YAML files, and this structure also fails normal
workflow validation even if renamed to YAML. Naming inputs, choosing outputs,
reviewing the task, and exporting workflow YAML use the `author` command; see
[AUTHORING.md](AUTHORING.md). The
recorder does not automatically select title/price outputs or register a tool.

## Code organization and tests

```text
src/browser_to_mcp/recorder/
  __init__.py         public recorder types
  draft.py            versioned models, preflight, safe exclusive saving
  capture.py          event validation, fill grouping, diagnostics
  session.py          browser lifetime, domain boundary, event bridge
  command.py          terminal controls and explicit save/discard choices
  assets/
    capture.js        browser event listeners and target selection
```

The small JavaScript asset runs inside Chromium because DOM event listeners
run in the page. Python owns the application. The asset is bundled in the
Python package; no Node.js, npm, framework, or separate build step is needed.

Run `uv run pytest tests/recorder`. The tests use real Chromium and a controlled
local HTTP page, verify captured targets through the replay engine, and cover
privacy, ordering, unsupported interactions, cancellation, browser closure,
exclusive saving, and draft exclusion from tool registration.
