# Changelog

## 0.0.1a1 - 2026-07-27

- Published the first private V0 pre-release for repository collaborators.
- Included the reproducible Hacker News workflow, local MCP demonstration,
  redacted trace format, and read-only policy boundary.
- Added the clean-checkout demo, contribution guide, security policy, and
  release checklist.
- Added explicit waiting for asynchronously rendered Hacker News results and
  tag-driven distribution packaging.

## Unreleased

- Add a loopback-only local interface for task creation, job questions, previews,
  independent evaluation, full review, explicit publication and run history (#65).
  Reuse the existing worker/evaluator/library/replay services, support client-led
  or worker-provider creation, and protect local controls with origin/host/token checks.

- Add a local version library with evidence-bound review, atomic publication,
  rollback, retirement, audit history and pinned MCP/trace identities (#64).

- Add a one-tab Chromium recorder for public read-only demonstrations (#46).
  Save versioned local drafts with executable targets, coalesced typing,
  ordered diagnostics, explicit partial saves, and exclusive private files.
  Keep browser event listeners in the recorder's bundled assets directory;
  honor saved selectors on replayed key presses.
- Validate named output producers before browser startup and share strict
  primitive parsing across CLI, MCP, and typed JSON traces (#45). Preserve
  valid zero/false values and redact nested sensitive text; omit failure
  screenshots for runs with provided sensitive inputs.
- Reject unsupported `retry: one` and `retry: bounded` settings consistently
  across validation, dry-run, replay, and MCP startup (#44). Preserve the
  existing non-retryable contradiction error and single-attempt execution.
- Encode navigation inputs once as URL data in query values and whole path
  segments, preserving fixed parameters and fragments (#43).
- Reject unsupported URL placeholder positions before browser startup; retain
  runtime domain checks and plain-text substitution for other action fields.
- Verify decoded inputs through CLI and MCP replay against a local HTTP server.
