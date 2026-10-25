# Follow a tool-creation job

A job is one attempt to prepare a tool draft. It has its own ID, saved status,
limits, and any question that needs an answer. You can close your MCP client,
reconnect, and read the same job. Reading its status never starts it again.

This implements [#61](https://github.com/browser-to-mcp/browser-to-mcp/issues/61).
The first supported job takes an **existing public workflow YAML file**, checks
its schema and permissions, builds its MCP schema, and prepares a local draft
for review. That job does not open the website or call a model.
The [task builder](TASK_BUILDER.md) adds a second job type that reads a public
page and asks a configured local model to propose a draft. It uses the same
status, questions, cancellation, and resource limits described here.
Independent browser testing and publication belong to #63 and #64.

## Try it

Follow [the setup guide](DEMO.md) first. The local worker supports macOS and Linux.
Run it from the repository in one terminal:

```bash
uv run browser-to-mcp jobs worker
```

Leave that terminal open. The worker waits quietly for jobs. It owns the work;
CLI commands and MCP connections are clients of the same saved job directory.

In another terminal, generate a request ID:

```bash
uv run python -c "import uuid; print(uuid.uuid4())"
```

Copy that UUID into the next command in place of `PASTE_REQUEST_ID`:

```bash
uv run browser-to-mcp jobs start workflows/hn_search.yaml --request-key PASTE_REQUEST_ID
```

The response contains the job's `id`. This is different from your request ID.
Use the job ID in place of `PASTE_JOB_ID`:

```bash
uv run browser-to-mcp jobs read PASTE_JOB_ID
```

When its state becomes `ready_for_review`, the response includes:

- `artifact.filename`: the prepared draft inside `.browser-to-mcp-jobs/`;
- `artifact.manifest_digest`: the exact workflow being reviewed;
- `question.id`, `question.choices`, and the current `revision`.

Open the draft file and review it. The `.candidate` extension keeps it out of
normal YAML tool discovery. To keep this draft, copy the current values into:

```bash
uv run browser-to-mcp jobs answer PASTE_JOB_ID --question-id PASTE_QUESTION_ID --revision PASTE_REVISION --value keep
```

The worker records that decision and the job becomes `completed`. Choosing
`discard` cancels it. **Keeping a draft does not run or publish a tool.** The
existing default MCP tools are unchanged. The prepared draft has passed static
validation; it has not passed a new browser test or an independent test gate.

To cancel a job:

```bash
uv run browser-to-mcp jobs cancel PASTE_JOB_ID
```

For a running job, this sets `cancel_requested: true`. Keep reading its status
until it becomes `cancelled` or another terminal state. Cancellation cannot
undo a job that already completed before the request arrived.

Stop the worker with Ctrl+C. A running phase is stopped and marked interrupted;
queued jobs stay queued. Restarting the worker can process queued jobs but never
repeats a phase left in `running` by a crash.

For scripts, `jobs worker --once` processes at most one queued phase and exits.
A job that needs review therefore needs one worker invocation before the answer
and another after it. Status reads do not act as worker invocations.

## What the states mean

| State | Meaning |
| --- | --- |
| `queued` | Waiting for the worker to start the next phase |
| `running` | One phase is active; usage counts show reserved work |
| `waiting_for_input` | A builder needs a listed choice before its next phase; no browser is held open |
| `ready_for_review` | The checked draft is available and the worker is waiting for your keep/discard choice |
| `completed` | The job kept its draft; this is not publication |
| `failed` | Work stopped with a fixed error code and explanation |
| `cancelled` | Work was cancelled or its draft was discarded |

`stage` names the current phase. The first job type uses `prepare` and `finish`.
A new job starts queued. Running work may ask a question, become ready for
review, complete, fail, or be cancelled. Answering a current question queues
only its declared next phase. Failed, cancelled, and completed jobs are never
reopened. Start a new attempt with a new request ID when needed.

The service also supports a fixed `output_shape` question (`single` or `list`)
for builder integration. The current preparation worker does not need that
question. It asks only whether to keep the validated candidate.

Each question has a unique ID. An answer must include that ID, the current job
revision, and exactly one listed choice. An old, repeated, or competing answer
is rejected. Answers are not free-text boxes for passwords, tokens, or login
codes. The job service has no login or credential input field.

## Start once, follow the same attempt

Keep the request ID used by `start`. Sending the same ID, candidate, and limits
returns the original job, even if it has finished or failed. A differently
formatted candidate that normalizes to the same workflow is the same request.
Changing the candidate or limits while keeping the ID produces `conflict`.

Request IDs must be UUID v4 values. They are stored as hashes rather than raw
request text. Job IDs are also UUID v4 values. Concurrent starts are serialized
in a local SQLite transaction, so two clients cannot create two jobs for the
same request ID.

There is one worker per job directory, protected by an operating-system lock.
Other worker processes are refused. Clients may reconnect or run concurrently;
they do not own the browser or background phase process.

Status reads are read-only. They do not create a directory, claim work, run
cleanup, change deadlines, or repair a crashed worker. If the worker is stopped,
the last saved state remains visible, along with its `deadline`. Restart the
worker to reconcile interrupted phases and expired waiting jobs.

## Limits

Browser and provider budgets are reserved before those operations start.
Output size is checked before generated data is used further or a draft is saved.
A reserved model request is charged conservatively even if it later fails;
reservations are not refunded or silently retried.

| Start option | Default | Allowed range | What it limits |
| --- | --- | --- | --- |
| `--wall-seconds` | 900 | 1–86,400 | Time from job creation, including queue and human waiting time |
| `--browser-actions` | 0 | 0–1,000 | Browser startup and metered operations across phases |
| `--model-requests` | 0 | 0–100 | Inference requests across phases |
| `--tokens` | 0 | 0–1,000,000 | Reserved provider token upper bounds |
| `--cost-microusd` | 0 | 0–100,000,000 | Reserved provider cost; 1,000,000 units means US$1 |
| `--output-bytes` | 262,144 | 1–1,048,576 | Cumulative generated text and saved draft bytes |

For example:

```bash
uv run browser-to-mcp jobs start workflows/hn_search.yaml --request-key PASTE_REQUEST_ID --wall-seconds 600 --output-bytes 65536
```

The existing-YAML preparation job uses no browser actions, model requests,
tokens, or provider cost. Raising those limits does not change its job type.
The separate [task builder](TASK_BUILDER.md) uses metered browser operations and
a configured local model. Its adapter reserves the exact input-token count plus
the allowed output tokens before inference. It makes no billable API request;
hardware and electricity costs are not measured. There is no cloud provider or
cloud pricing estimate in this release.

The source candidate and its normalized form are each limited to 256 KiB.
YAML aliases and anchors are refused. Browser text and model responses are
charged against the output budget before the worker can use them further;
the saved draft is charged too. Counts are reservations, not a claim about
actual provider billing.

Time waiting for a person counts towards the wall-clock deadline. Expired
answers cannot resume work. The worker checks waiting deadlines about once a
second and active phases more frequently. Browser and process cleanup may take
up to several extra seconds after a stop request; no further phase is queued.

## Cancellation and crashes

Each phase runs in its own local process group. The worker sends shutdown by
closing a private pipe; the phase also notices that pipe closing if the worker
is killed. It cancels its task and closes its managed browser and helpers.
If a phase is killed or refuses to stop, the owner terminates the remaining
process group. The next worker marks any interrupted `running` phase as failed,
without replaying it.

A completed phase is not repeated on reconnect. Answering a question advances
to a saved next phase; it does not replay earlier website actions. The runtime
uses the shared [browser rules](BROWSER_POLICY.md) and the immutable request’s
allowed domains when a trusted builder uses a browser. A phase reuses its one
browser context; the builder cannot pass a larger domain list to the runtime. Job requests cannot supply shell commands, browser scripts, or a
custom worker executable.

The current candidate-preparation phases use only local files. Tests exercise
crash and cancellation cleanup with a separate test worker that owns a real
Chromium page and child process.

## Saved data and cleanup

The default directory is `.browser-to-mcp-jobs/`, which is ignored by Git.
Choose a new private directory when using a different location:

```bash
uv run browser-to-mcp jobs --directory /absolute/private/job-directory worker
uv run browser-to-mcp jobs --directory /absolute/private/job-directory read PASTE_JOB_ID
```

Use that same absolute directory for every client and the worker. The store
requires a private directory and creates files readable and writable only by
the local owner. This release is for one local owner, not hosted users or a
shared network drive.

The SQLite record contains state, times, limits, counts, fixed public questions,
error codes, and artifact identities. It does not contain page contents, raw
source paths, provider prompts, credentials, raw exceptions, or arbitrary user
messages. The candidate itself is a separate private artifact needed to prepare
and review the draft. Only public workflow data belongs there. Known credential
patterns and unsafe workflow URLs are refused; this is not a detector for every
possible secret string.

The worker removes input artifacts once they are no longer needed, and removes
discarded or failed drafts. Completed drafts and terminal job records are kept
for seven days. Small request receipts (hashed request ID, hashed request content,
and job ID) are kept until the store is deliberately removed. They contain no
candidate text or answers. An old request returns `expired_key` after its job
record is removed; it never silently becomes a new attempt.

The store allows up to 1,000 retained jobs and 10,000 request receipts. Cleanup
can free job slots. At the receipt limit, use a new private directory for new
attempts and keep the old directory for clients following old IDs. Removing a
store deliberately removes its duplicate protection; clients must not treat a
new directory as the old job history.

Cleanup runs in the worker. You can also request it explicitly:

```bash
uv run browser-to-mcp jobs cleanup
```

If neither the worker nor cleanup runs, files remain on disk. Reading status
does not delete anything. Cleanup does not reclaim an actively running phase;
worker recovery owns that decision.

## Local MCP access

Run `jobs worker` separately, then configure a local STDIO connection using the
same job directory. For example, replace both absolute paths below:

```json
{
  "mcpServers": {
    "build-jobs": {
      "command": "/absolute/path/browser-to-mcp/.venv/bin/browser-to-mcp",
      "args": ["jobs", "--directory", "/absolute/private/job-directory", "server"]
    }
  }
}
```

This job-management server offers `start_build_job`, `read_build_job`,
`answer_build_job`, and `cancel_build_job`. `start_build_job` accepts the public
candidate YAML as `candidate`, a UUID `request_key`, and optional `budgets`
using the underscore versions of the CLI option names.
It also offers `start_task_build_job` for the task-description format in the
[task builder guide](TASK_BUILDER.md).

CLI and MCP call the same service and return the same job fields and error
codes. CLI operation errors use exit code 2 and JSON on stderr; MCP operation
errors set `isError: true` and include the same JSON fields. The regular
workflow MCP server remains a separate command and receives no new callable
tools from a completed build job.

## Checks

Run the controlled job checks with:

```bash
uv run pytest -q tests/jobs
```

They cover state transitions, repeated and concurrent starts, stale questions,
all budget dimensions, waiting deadlines, retention, real MCP reconnects,
worker shutdown and abrupt crashes, browser restrictions, and process cleanup.
They do not use public websites or live model services.
