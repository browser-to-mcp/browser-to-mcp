# Local tool versions and publication

The library gives a public tool a stable ID and numbered versions. Importing
or finishing a build never publishes a tool. A version must pass the existing
independent evaluation checks and receive explicit owner approval first.

The library is local and single-owner. Keep its directory private. It contains
public candidate YAML, review evidence, traces, and audit records in one SQLite
database, created with owner-only file permissions. It is not a multi-user
service or a tamper-proof record against the computer's owner.

## Import and test

Existing manually reviewed YAML and generated job candidates use the same
explicit migration path. Import preserves the exact source bytes and their
hash, workflow manifest digest, allowed domains, and original workflow version.
The library version number is separate from the workflow's `version` field.

```bash
uv run browser-to-mcp library import workflows/hn_search.yaml \
  --origin 'reviewed YAML migration' --display-name 'News search'
uv run browser-to-mcp library list
```

Record the returned `tool.id` and `version.version`. Import validates the normal
workflow policy and compiles the normal input/output schemas. Import alone leaves
an uncallable `draft`. Only public candidates supported by the existing candidate
validator are accepted; no extra capabilities or domains are inferred.

Prepare and independently review a plan using [DRAFT_CHECKS.md](DRAFT_CHECKS.md).
Evaluate the exact original file and attach the resulting attempt. Replace the
uppercase placeholders below with returned IDs, numbers, hashes and local paths:

```bash
uv run browser-to-mcp evaluate candidate.yaml plan.json \
  --suite-sha256 sha256:PLAN_HASH --evidence-dir checks/attempt-1
uv run browser-to-mcp library test TOOL_ID 1 \
  --evidence-dir checks/attempt-1 --suite-sha256 sha256:PLAN_HASH
uv run browser-to-mcp library review TOOL_ID 1
```

The independently approved plan hash must be supplied explicitly. The library
rechecks the candidate bytes, manifest, plan, coverage and actual trace outcomes
using the existing verifier. It retains the report, original plan/candidate and
referenced traces. Evidence must fit within 32 MiB. It does not browse again or
ask a model to grade results. Failed verification gives the version `failed`;
missing or unreadable evidence is rejected without changing it. Import a fresh
version for a corrected candidate or another test attempt.

Review prints the complete workflow, inputs, outputs, actions, safety declarations,
domains, success assertions, original evidence and approval. Inspect the test
plan's expected answers and any remaining limitations; passing software checks
alone does not establish that the task is useful or that the plan is sufficient.

## Explicit publication

After reviewing, use the returned `tool.revision` and `version.sha`:

```bash
uv run browser-to-mcp library publish TOOL_ID 1 \
  --revision REVIEW_REVISION --candidate-sha256 sha256:CANDIDATE_HASH --approve
uv run browser-to-mcp library server --trace-dir traces
```

Approval is bound to the exact immutable candidate and retained evidence. A
changed revision, new draft, wrong hash, failed tests, or absent approval refuses
publication. Cancelling before `publish` leaves no callable version. Publication,
status changes, approval and audit entry commit in one SQLite transaction.
Concurrent approvals cannot both use the same revision. Callable names are
reserved uniquely across the library, including retired tools, to avoid accidental
reuse. Display names may differ from callable names and stable IDs.

The library server uses the existing compiler, MCP server and replay engine.
Only published versions are registered. It validates the complete snapshot before
creating tools; an invalid published record prevents partial registration. A
server keeps its startup snapshot, including for in-flight calls. Restart it to
adopt publication, rollback or retirement. To stop new calls immediately after
retirement, stop the old server too; retirement does not kill its pinned runs.

MCP responses identify the pinned tool ID, library version and candidate hash in
`_meta.publication`. Saved traces contain the same identity in
`metadata.publication`, alongside the existing manifest/workflow identity.
Typed output schemas and the meaning of workflow outputs remain unchanged.
The ordinary `server --workflow-dir` command remains an explicit developer YAML
mode; it does not enforce library approvals. Use `library server` for this lifecycle.

## New versions, rollback and retirement

```bash
uv run browser-to-mcp library import updated.yaml \
  --tool-id TOOL_ID --origin 'build job JOB_ID'
```

This appends a new draft without changing the live version. It cannot edit a
previously tested or approved source. The callable name must remain the same;
use a new tool ID for another callable name. Test and review the new version
before publishing it. Source/evidence/approval records are immutable through the
service; lifecycle state and the live pointer are separately tracked.

```bash
uv run browser-to-mcp library review TOOL_ID 1
uv run browser-to-mcp library rollback TOOL_ID 1 \
  --revision CURRENT_REVISION --candidate-sha256 sha256:OLD_CANDIDATE_HASH
uv run browser-to-mcp library retire TOOL_ID --revision CURRENT_REVISION
uv run browser-to-mcp library history TOOL_ID
```

Rollback requires a previously approved version and reverifies its retained
checks. Its explicit command is the owner's choice to reactivate that version,
including after retirement. A formerly published version becomes `tested` when
another version replaces it, and keeps its original approval. Retirement clears
the live pointer and marks that live version `retired`. No source, evidence,
approval or run trace is deleted. Every lifecycle change records its timestamp,
version and incremented revision. Read a fresh revision after each change.

The lifecycle is `draft → tested → published`, with `draft → failed` for failed
checks, `published → retired`, and explicit rollback to an approved version.
There is no automatic publication, hot reload, automatic repair or deletion.

## Checks

`uv run pytest tests/library` covers exact evidence binding, stale reviews,
duplicate names, concurrent approvals, rollback, retirement and trace provenance.
The real Chromium and separate-process MCP tests reuse the original controlled
evaluation site and independent expected answers. Live website reliability and
human usability are separate measurements; this feature adds no such claims.
