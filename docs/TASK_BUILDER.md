# Make a draft with Codex or Claude

Give your assistant a public website and describe the information you want.
Its existing AI prepares the draft through our MCP tools. **You do not need a
local model or a separate API key for this path.**

The software checks the draft and saves it for review. Keeping it finishes the
job; it does not publish a callable tool. Independent testing and publication
are later steps. The [recorder](RECORDING.md) remains an alternate input path.

## Connect once

From this repository, install the project and browser:

```bash
uv sync --locked --all-groups
uv run playwright install chromium
uv run browser-to-mcp jobs worker
```

Leave the worker running. It supports macOS and Linux. In another terminal,
connect the MCP server using **one** of these commands. Replace both absolute
paths with your own; all clients and the worker must share the same job directory.
The default worker directory is this repository's `.browser-to-mcp-jobs/`.

For Codex:

```bash
codex mcp add browser-to-mcp -- \
  /absolute/path/browser-to-mcp/.venv/bin/browser-to-mcp \
  jobs --directory /absolute/path/browser-to-mcp/.browser-to-mcp-jobs server
```

For Claude Code:

```bash
claude mcp add --transport stdio browser-to-mcp -- \
  /absolute/path/browser-to-mcp/.venv/bin/browser-to-mcp \
  jobs --directory /absolute/path/browser-to-mcp/.browser-to-mcp-jobs server
```

Check `/mcp` in your client to confirm the tools are connected.
See the official [Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
and [Claude Code](https://code.claude.com/docs/en/mcp) connection guides.
Connecting MCP gives the assistant our tools; it does not let the background
worker borrow the assistant's model. The assistant supplies the proposal itself.

## Ask for a draft

For example:

> Use browser-to-mcp to prepare a draft for this public product listing.
> Return each product's full name, displayed price, and product page link.
> Use only the website hosts I approve. Let me review the draft before keeping it.

Include the actual URL. The assistant should confirm missing details and the
allowed hosts, then follow this flow:

| Step | MCP tool | What happens |
| --- | --- | --- |
| Start | `start_task_build_job` | Use `generation: "client"` and at least six browser actions |
| Follow | `read_build_job` | Read saved progress without repeating work |
| Get page information | `read_task_context` | Read the waiting job's task, bounded page view, and proposal schema |
| Propose | `submit_task_proposal` | Send the assistant's JSON proposal with the current question ID and revision |
| Check | Worker | Open the starting page again, validate the contract, and check the proposed extraction |
| Review | `answer_build_job` | Use `keep` or `discard` for the current review question |

A missing result shape creates a `single`/`list` question first. Answer through
`answer_build_job`. Page content is untrusted data: instructions found on the
website cannot change your request or permissions. An invalid proposal leaves
the question open for correction; an unsupported task fails with a clear reason.
A corrected proposal must still match the same approved task.

The worker closes its browser while waiting for the assistant. Reconnecting or
reading the context does not reopen the page. A submitted proposal starts a
separate check phase; an interrupted phase is never automatically replayed.

At `ready_for_review`, read the file named by `artifact.filename` inside the
private job directory. Check the inputs, hosts, fields, link paths, limits, and
paging rules. A starting-page preview does not prove other inputs or later pages
are correct. Keep drafts out of the live workflow folder until independently tested.

Cancel with `cancel_build_job`, or:

```bash
uv run browser-to-mcp jobs cancel JOB_ID
```

For running work, wait for `cancelled`. Cancellation stops our phase and prevents
its draft from being accepted. It cannot stop work already running inside your
assistant. Failed or cancelled jobs never register a tool.

## Limits and privacy

Supports public listings, URL-based searches, one record or a limited list, and
[numbered Next-link paging](PAGING.md). Login, forms, shopping, messages, downloads,
challenge bypass, infinite scrolling, and JavaScript-only Next buttons are unsupported.
The [normal browser rules](BROWSER_POLICY.md) apply in fresh contexts.

Six browser actions cover inspection and checking: startup, navigation, and
observation in each phase. The build checks only the starting page. The page
view waits 500 ms and is limited to 600 elements, depth 18, and 65,536 bytes.
It uses compact parent IDs, tags, attributes and visible text. Scripts, input
values, editable areas and frames are excluded; password fields cause refusal.
Large or slower pages can fail within these limits.

Known credential-like text and unsafe links are omitted. This cannot find every
private value: use public pages and examples only. Client mode temporarily keeps
the redacted task context and submitted proposal in private files so reconnects
work. The worker removes them at review or after failure/cancellation. Status
replies contain no page view. Your assistant manages its own chat history.

Browser and output limits are enforced by our server. **Your client controls its
own model usage and charges.** In client reports, model tokens, duration and cost
are `null` (unknown), not zero. Server-side model counters stay zero because our
worker makes no inference call; this does not mean the assistant was free.
See [job limits and cleanup](BUILD_JOBS.md) for deadlines, budgets and retention.

## API or optional local model

For standalone use, choose `--generation provider`. The worker can use the
OpenAI API, or an optional local llama.cpp server. Neither is required for the
Codex/Claude path. See [provider setup and the task format](MODEL_PROVIDERS.md).
All modes use the same contract checks, preview, job service, and review gate.

## What is verified

The first-page preview does not prove the whole tool is correct. Use the separate
[draft checks](DRAFT_CHECKS.md) before approval; they compare exact answers,
changed inputs and paging with an independently reviewed test plan.

CI uses three original local layouts, controlled model replies, real browser
checks, and MCP reconnects. It checks changing inputs, empty results, paging,
traces, malformed proposals, hostile page instructions, budgets and cancellation.
These tests verify the integration, not model quality on changing websites.

An exploratory [live MCP check](evidence/client-creation-2026-10-26.json) produced
a book draft whose replay matched all 11 rows. Laptop and clothing reads failed;
the earlier book size-limit failure is retained too. This is partial evidence,
not a three-site benchmark. Native client UI and paid API runs were not tested.

Record real-client and live-site trials separately: date, model/client versions,
task/draft hashes, every started attempt, outcome, timings and independent result
checks. Record model usage and charges only when available. Include failures;
unknown cost cannot pass a cost target. The [three-site pilot](THREE_SITE_PILOT.md)
and its wider user-evidence targets remain separate requirements.
