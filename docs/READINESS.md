# M3 readiness report

Issue #17 verifies that the local replay path produces stable, diagnosable
outcomes for the failure modes that matter to the V0 safety boundary. The
fixtures are deterministic: they do not contact a website or require browser
state.

## Categories

- `success`: the declared safety invariant held.
- `refusal`: validation or an output assertion rejected unsafe or malformed
  input before it could be accepted as a result.
- `timeout`: a declared wait bound was exceeded.
- `infrastructure_failure`: a changed page dependency, such as a selector,
  prevented replay and left a diagnostic path.

## Reproduce the gate

Run the fixture matrix locally:

```bash
uv run python scripts/readiness_report.py --runs 10
```

The command emits JSON with every fixture's expected category, observed
classification counts, success rate, and minimum/mean/maximum latency. It
returns exit code `0` only when every run matches its expected category.

The full test suite also exercises the same matrix. The release workflow runs
this command before building an artifact, so a failed M3 fixture blocks a
future release.

## Baseline

The following baseline was recorded on 2026-07-27 on arm64 macOS 26.5.2 using
ten runs per fixture. Latency is fixture-harness overhead in milliseconds, not
live browser or network performance.

| Fixture | Expected category | Matching runs | Mean latency |
| --- | --- | ---: | ---: |
| Changed selector | `infrastructure_failure` | 10/10 | 0.161 ms |
| Delayed page | `timeout` | 10/10 | 0.132 ms |
| Malformed output | `refusal` | 10/10 | 0.009 ms |
| Unauthorized domain | `refusal` | 10/10 | 0.012 ms |
| Hostile page content | `success` | 10/10 | 0.022 ms |

The hostile-content fixture confirms that page text stays output data, does
not alter the declared workflow or allowlist, and has sensitive values redacted
before trace persistence. The changed-selector and delayed-page fixtures retain
their step paths and stable failure categories so a trace can explain the
failure without exposing a sensitive input.
