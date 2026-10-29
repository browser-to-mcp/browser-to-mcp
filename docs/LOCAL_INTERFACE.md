# Create and run tools in a local browser interface

The interface wraps the existing task builder, supervised worker, independent
evaluator, tool library and replay engine. It adds no alternate workflow engine,
model-generated executable code, automatic publication or hosted service.

## Start

Install the project and Chromium using the [README](../README.md), then run:

```bash
uv run browser-to-mcp ui
```

Open the private URL printed in the terminal. It contains a temporary capability
in its fragment. Do not share it. The interface removes the fragment from the
address bar and keeps the token in tab session storage for reloads. A restart
creates a new token; reopen the new URL. The token is not a model API key.

The interface binds only to `127.0.0.1`, using an unused port by default. It does
not offer a network bind option. Use `--port 8765` if a fixed loopback port is
needed. Its HTTP endpoints require the exact host, same browser origin and the
private bearer token. No permissive CORS is enabled. Assets have restrictive CSP,
no-store and anti-framing headers. Page data is rendered as text, never HTML.
This is a local single-owner application, not a hosted access-control system or
a boundary against other programs running as the same computer owner.

Storage defaults to `.browser-to-mcp-ui`, `.browser-to-mcp-jobs` and
`.browser-to-mcp-library`. All are ignored by Git. The UI directory must be
private (0700) and can have only one interface owner. Override the locations with
`--directory`, `--jobs-directory`, and `--library-directory` as needed. The
interface starts the existing supervised jobs worker. Do not start a second
worker for that same job directory. Build-job MCP servers can connect to it.
The existing worker requires a POSIX platform, such as macOS or Linux.

## Create a task

Open **New task** and supply:

- Tool name, public starting URL, plain-English task description and explicit
  allowed website hostnames. A declared host includes its subdomains, as in CLI.
- Names, descriptions, primitive types and demonstration values for inputs.
  These examples are samples for binding, not schema defaults.
- Names, descriptions and supported primitive types for returned record fields.
  Link fields also need an explicitly allowed product-link path prefix.
- Single record or list shape, item/page limits, optional empty-list behavior
  and optional unique result field.

Only the builder's supported public task subset is offered. Its reusable inputs
bind to URL query values or path segments. Every generated input is required.
The interface does not add arbitrary interactive planning, authentication,
side-effect capabilities or unsupported optional/default-input semantics.

Choose a creation mode:

**Connected assistant (default).** No separate model installation or worker API
key is required. Copy the build-jobs configuration from **MCP setup** into your
MCP client and restart/reconnect the client. The assistant reads the waiting
job's context and submits a proposal with the existing `read_task_context` and
`submit_task_proposal` tools. It must treat website content as untrusted data.
The interface accurately stays in the waiting-for-proposal state until that
proposal arrives; refreshing does not invent a proposal or restart exploration.
Worker model calls are zero in this mode; the assistant's model cost is unknown.

**Worker API provider.** Configure an existing provider in the trusted worker
environment before starting the interface; see [MODEL_PROVIDERS.md](MODEL_PROVIDERS.md).
The page shows whether configuration is present, without showing keys. Explicitly
grant model-request, token and cost budgets. Public task/page data may be sent to
the configured provider. Never paste provider URLs, passwords or API keys into
task fields. Provider credentials stay in the worker environment.

Click **Start build** once. In **Build jobs**, follow saved progress, resource
usage, provider/client accounting, questions and failures. Answer only the current
question. Cancel unwanted builds. For a corrected task, explicitly create a new
attempt; old failed or cancelled attempts remain visible until normal job
retention expires. Reloading only reads state, never starts another job.

## Preview, test and review

When ready, **Inspect draft & schemas** shows the actual saved contract and
compiler-generated schemas. **Run this draft preview** accepts typed JSON inputs
and uses the existing replay engine. This is a bounded, explicitly requested
preview, not independent correctness evidence and not a published MCP tool.
Results and failures appear in **Run history**. No browser run starts on refresh.

Answer the existing **keep** question to retain the checked draft. Then choose
**Keep in library for independent tests**. Import does not publish. The interface
saves and tracks the draft internally, so no workflow-file management is required.
Choose **New tool**, or explicitly select **New version of …** in **Save draft to**
to append a corrected or updated version to an existing tool ID. Callable names
must match. Silent name reuse is refused. Each completed job is imported once;
repeated imports reopen its same saved version.

In **Review & tests**, inspect the complete workflow, inputs, outputs, selectors,
action timeouts, permissions, safety declarations, success checks and limitations.
The JSON view includes the exact candidate identity and retained test evidence.

Prepare an independent JSON test plan in the editor using
[DRAFT_CHECKS.md](DRAFT_CHECKS.md). The bundled `examples/evaluation/catalog.json`
shows the format. Expected results must come from checked answers, not a copied
model preview. Required coverage includes a known successful result, changed
inputs, invalid input and changed-page behavior, with additional list/pagination
coverage as applicable. This release's test-plan and run-input editors use JSON;
it is a technical owner interface, not a no-code usability claim.

Explicitly approve the plan and click **Evaluate this exact version**. The UI
saves a private plan, pins its exact bytes and uses the existing evaluator and
library verifier. Test operations are bounded by the plan's normal per-run and
wall limits; at most two preview/test/run operations are active at once. A passed
refusal is distinct from a successful data result. Failed, skipped, blocked,
changed or incomplete required evidence cannot pass publication checks.

Open **Review tested version**, read its full evidence, and check the final
review box before **Publish reviewed version**. The service enforces evidence,
approval, exact candidate hash and revision checks even if page controls are
modified. A changed draft needs fresh tests and review. Cancelling review or
leaving the page never publishes. Publication creates the existing library's
atomic approval/audit record.

## Run and connect

From **Tools**, select **Run tool**, enter typed inputs and run the published
version. The operation pins that approved version before queueing. The result and
trace identify the stable tool ID, library version and candidate hash. Errors
retain classified workflow details and a trace reference when replay provides one.
Unexpected service failures show a safe correction message rather than raw page
or parser details. Completed operations may contain a failed workflow outcome;
inspect the result's status/category as well as the operation state.

**MCP setup** provides copyable JSON using the current installed console command
and the actual job/library directories. It configures both the build-jobs server
and published-tool server over local STDIO. It contains no provider credentials.
Start or restart external library servers after publication, rollback or retirement:
those servers intentionally keep their startup snapshot, including in-flight runs.
The UI run button reads the current published snapshot for each new explicit run.

Use **Version history** to inspect earlier versions and explicitly restore a
previously approved version, or retire the current tool. Evidence and history
remain available. See [TOOL_LIBRARY.md](TOOL_LIBRARY.md) for library semantics and
explicit YAML migration. The developer YAML server remains a separate mode.

## Persistence, cancellation and limits

The UI keeps operation/run history in private SQLite storage. Mutating async
operations have persistent idempotency keys; replaying the same request cannot
start duplicate work. A reload reads the current state. After service restart,
unfinished UI operations become interrupted and require an explicit new attempt.
Existing build jobs follow the shared worker's recovery and retention rules.

Cancel build jobs in **Build jobs** and active preview/test/run operations in
**Run history**. Ctrl+C shuts down the local service and its owned worker/browser
resources. Cancellation does not undo already completed read-only website actions
or a publication that already committed. No completed or interrupted operation is
automatically restarted. Concurrent/stale publication requests fail closed.

HTTP requests are limited to 1 MiB. Plans/evidence retain the existing evaluator
and library limits. Preview and published UI runs have a 60-second whole-run bound.
Public, non-sensitive data only. Do not expose the service through a reverse
proxy, share its token, or use it for login, payments, uploads, messaging or writes.
No broad website reliability, accessibility certification or human onboarding
time is claimed from automated checks. Real user evidence remains separate.

## Implementation and verification

The Python `interface/` package owns the thin HTTP adapter, async operation tracking
and command entry point. `interface/assets/` contains the separate HTML, CSS and
browser script, shipped in the Python wheel without Node.js or another build step.

`uv run pytest tests/interface` covers local access controls, stale/unapproved
publication, failed checks, cancellation, interrupted history, real browser form
interaction and a real MCP-supplied proposal through the complete pilot path.
The controlled pilot checks two named book fields, independent expected answers,
new-input replay, persisted history and compiler schemas. CI uses local fixtures;
live-site and willing-human walkthrough measurements remain separate.
