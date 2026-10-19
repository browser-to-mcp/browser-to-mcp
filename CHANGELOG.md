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

- Reject unsupported `retry: one` and `retry: bounded` settings consistently
  across validation, dry-run, replay, and MCP startup (#44). Preserve the
  existing non-retryable contradiction error and single-attempt execution.
- Encode navigation inputs once as URL data in query values and whole path
  segments, preserving fixed parameters and fragments (#43).
- Reject unsupported URL placeholder positions before browser startup; retain
  runtime domain checks and plain-text substitution for other action fields.
- Verify decoded inputs through CLI and MCP replay against a local HTTP server.
