# Verify recording through to MCP

This check is **observed by automated software using real Chromium and a real
MCP server**. It starts with a captured browser recording, answers the normal
authoring prompts, clicks page text to choose outputs, saves YAML, and calls the
saved tool through the normal server. Replay is not replaced with a test response.

The local catalog is original HTML served only on `127.0.0.1`. Every generated
workflow explicitly allows that host. No account, saved browser profile, public
website, or permission exception is needed.

## Run the checks

From a clean checkout, install dependencies and Chromium:

```bash
uv sync --locked --all-groups
uv run playwright install chromium
```

Run the journey and save its measured results:

```bash
BROWSER_TO_MCP_JOURNEY_REPORT=reports/journey.json \
  uv run pytest tests/authoring/test_journey.py -q --junitxml=reports/journey.xml
```

Use `uv run pytest` for the full regression suite. On Linux, Chromium may also
need system packages; CI installs them using `playwright install --with-deps chromium`.
The report directory is ignored by Git. CI uploads the JSON report and both test
result files as `browser-to-mcp-test-evidence`, even when a test fails. Artifacts
are retained for fourteen days.

## What the software observes

| Requirement | Evidence |
| --- | --- |
| Actual recording and review | `create_workflow` in `tests/authoring/journey.py` opens fresh Chromium, saves a captured draft, runs normal authoring, and uses real page clicks to select outputs. |
| Recorded link click | The link test records navigation, a click, and its resulting navigation. Export includes one click without adding a second navigation. |
| Normal loader and CLI | The typed-output test validates the exported YAML and runs it through the actual CLI process. |
| Actual MCP transport | The tests start the normal STDIO server in a separate process, list its tool, and call it with new inputs. |
| Thirty repeated cases | Each of the three pilot cases runs ten times through MCP. Every successful output and expected failure is compared exactly. |
| Correct results and types | Book title and price are strings. Additional selected fields check integer `0`, boolean `false`, and number `0.0` across CLI, MCP, and saved traces. Invalid numeric text fails consistently. |
| Missing and empty input | Missing MCP input is rejected before replay. A separate engine-start guard proves missing or empty required input starts no replay browser. |
| Missing, slow, broken, and challenge pages | The fixture returns a genuine 404 for the missing book, inserts slow content after 250 ms, removes the product heading for a broken target, or shows harmless challenge-like text without outputs. The workflow input contract stays unchanged. |
| Safe URL handling | A book slug containing spaces, `&`, `#`, and accented text returns the expected book through MCP. |
| Unsupported retries | Editing an exported step to `one` or `bounded` is refused by both CLI validation and normal server startup. No page request occurs. |
| Trace identity | Trace files are parsed against the trace schema and checked for workflow name/version, manifest digest, run ID, exact outputs and types, action outcomes, assertions, and exact failure category/path. |
| Cleanup | Capture and authoring contexts are closed; the MCP server process is confirmed exited; the local HTTP server stops even when its consumer fails. |

The negative-input engine guard is used only in the preflight test to detect an
unexpected browser start. Successful and failing browser-to-MCP runs use the real
engine without a replay replacement.

## Read the report

`reports/journey.json` records the tested commit, whether local files differed
from it, runtime versions, start/end timestamps, the generated workflow and its
digest, and each attempted input with its expected and observed result, trace ID,
and duration. Failed assertions leave `matched: false`; an interrupted sequence
cannot report `completed: true`.

A passing repetition check has `completed: true`, `attempts: 30`, and
`matching_outcomes: 30`. The ten missing-book runs are expected failures, not ten
successful book lookups. `first_correct_mcp_result_seconds` measures the automated
path from capture to its first correct MCP result with prerequisites already
installed. It is **not** a newcomer usability score. Installation was not timed by
this test.

The saved [2026-10-21 controlled report](evidence/journey-2026-10-21.json) records
one actual run. Use the CI artifact for each later PR's own results.

## Live websites and the remaining human check

The opt-in `examples/pilot_smoke.py` command is separate from these tests. The
[dated live observations](evidence/authoring-2026-10-20.json) preserve both the
initial 1/3 outcome and the corrected 3/3 outcome. They do not imply that every
website works, and live outages cannot fail this deterministic CI gate.

The [local walkthrough](ONBOARDING_CHECK.md) gives a person a repeatable path from
recording to a correct MCP result without relying on the public shop. The project
owner reported that their checks looked good, but no newcomer time or installation
time was supplied. That acceptance criterion remains unmeasured; #48 and #41 must
not be closed as fully complete on the strength of automated timings alone.
