# Build Plan

This is the implementation roadmap for the product specification. GitHub
issues are the execution record; this document keeps the dependency order and
release gates visible.

## Current Baseline

- Python 3.11+ with `uv`;
- Pydantic v2 workflow and trace models;
- Playwright Python replay engine;
- explicit read-only policy and domain allowlists;
- redacted JSON traces and failure screenshots;
- local recording drafts for public demonstrations;
- no hosted service, write workflow, or native WebMCP execution.

## Milestones

### M0: Bootstrap And Contract, complete

Issues [#1](https://github.com/browser-to-mcp/browser-to-mcp/issues/1) through
[#5](https://github.com/browser-to-mcp/browser-to-mcp/issues/5) established the
package, CI, typed workflow models, YAML loading, policy validation, and
canonical manifest digests.

### M1: Deterministic Replay And Trace, complete

Issues [#6](https://github.com/browser-to-mcp/browser-to-mcp/issues/6) through
[#9](https://github.com/browser-to-mcp/browser-to-mcp/issues/9) added replay and
dry-run commands, declared action execution, assertions, redacted traces,
provenance, isolated Playwright contexts, request enforcement, and bounded
cleanup.

### M3: Policy, Regression, And Readiness, complete

- [#10](https://github.com/browser-to-mcp/browser-to-mcp/issues/10) Enforce step
  risk and idempotency policy: complete.
- [#14](https://github.com/browser-to-mcp/browser-to-mcp/issues/14) Snapshot
  native WebMCP tool discovery: complete.
- [#17](https://github.com/browser-to-mcp/browser-to-mcp/issues/17) Build
  regression fixtures and readiness metrics: complete.

The M3 release gate is repeatable replay, stable refusal categories, and
diagnosable traces. The checked-in readiness report and release workflow block
publication when a required fixture fails.

### M2: Local MCP Server, complete

This milestone turns validated workflows into local typed MCP tools.

- [#13](https://github.com/browser-to-mcp/browser-to-mcp/issues/13): compile
  workflow inputs and outputs into deterministic tool schemas: complete;
- [#11](https://github.com/browser-to-mcp/browser-to-mcp/issues/11): serve valid
  workflows over local STDIO using the official MCP Python SDK: complete;
- [#12](https://github.com/browser-to-mcp/browser-to-mcp/issues/12): cover tool
  listing, invocation, invalid input, startup refusal, and cleanup: complete.

The server must reuse the existing loader, policy, replay engine, and trace
format. It must never register an invalid workflow.

### M4: Public Demo And Onboarding

- [#15](https://github.com/browser-to-mcp/browser-to-mcp/issues/15): publish a
  reproducible HN demo and first pre-release;
- [#16](https://github.com/browser-to-mcp/browser-to-mcp/issues/16): write the
  public onboarding and trace walkthrough.

This milestone starts only after M3 safety and repeatability gates pass.

## Next: Recorder Pilot

Issue [#42](https://github.com/browser-to-mcp/browser-to-mcp/issues/42) selects
the first recorder example. The task, expected results, allowed websites, and
local test-page requirements are in [PILOT.md](PILOT.md). The brief records
live browser checks and clearly marks the user needs that are still assumptions.

The parent milestone is
[#41](https://github.com/browser-to-mcp/browser-to-mcp/issues/41). Recorder
capture is implemented in #46; see [RECORDING.md](RECORDING.md). Guided workflow
setup and real browser-to-MCP authoring tests are implemented; see
[AUTHORING.md](AUTHORING.md). The independent timed newcomer walkthrough remains
open in #48, and the parent milestone must stay open until that evidence exists.
Recorded drafts do not expand the server's workflow list.

## Working Rules

- Keep the workflow contract reviewable and versioned.
- Keep CLI and future MCP calls on the same engine path.
- Fail closed on policy, domain, schema, and assertion failures.
- Add focused tests with every behavior change.
- Keep commits small enough to map to one implementation concern.
- Do not add speculative integrations before the current milestone’s exit gate.

## Deferred Work

Authenticated profiles, CDP attachment, native WebMCP
execution, automatic repair, remote transports, hosted execution, registries,
and enterprise controls remain post-V0 work. They require separate design and
security review rather than being folded into the local replay core.

## Definition Of Done

The first release is ready when a fresh developer can install the project,
edit `workflows/hn_search.yaml`, validate it, replay it with a new query, call
it from a local MCP client, and diagnose a deliberate selector failure from a
redacted trace in under ten minutes.
