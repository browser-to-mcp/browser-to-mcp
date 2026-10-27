# Check a draft before approval

A sample result is not enough. These checks compare a draft's answers with a
separate test plan. They use the normal typed inputs, browser rules and replay
engine. The same checks accept task-builder and recorder-exported workflow YAML.
No model is called, and no tool is published.

## Try the local example

Install the project and Chromium using [DEMO.md](DEMO.md). In one terminal, run:

```bash
uv run python tests/paging/paging_site.py --port 8767
```

In another terminal, review `examples/evaluation/catalog.json`. Its answers come
from the fixed local rows in [the pilot](THREE_SITE_PILOT.md), not the draft's
sample output. Calculate its file hash:

```bash
shasum -a 256 examples/evaluation/catalog.json
```

Replace `HEX` below with that hash. Each attempt needs a **new** directory:

```bash
uv run browser-to-mcp evaluate examples/paging/catalog.yaml \
  examples/evaluation/catalog.json --suite-sha256 sha256:HEX \
  --evidence-dir catalog-check-1
```

Read `catalog-check-1/report.md`, then inspect `report.json` and the linked
`traces/` files. A passed refusal means the tool failed safely; it does not mean
it returned correct data. Check saved evidence again without opening a browser:

```bash
uv run browser-to-mcp check-evaluation examples/paging/catalog.yaml \
  examples/evaluation/catalog.json --suite-sha256 sha256:HEX \
  --evidence-dir catalog-check-1
```

Exit codes: **0** = required checks passed; **1** = evaluation finished with
required gaps; **2** = invalid files, changed evidence or an interrupted command.
Interrupted evaluations save completed checks and mark unfinished checks untested.
They do not retry or overwrite earlier attempts. A hard process kill or disk
failure may leave no finished report; such an attempt cannot pass verification.

## Prepare your own test plan

Keep the plan outside the builder's control. A reviewer chooses its expected
answers and approves its hash **before** evaluation. Do not update an answer or
its approved hash just to make a failing draft pass. Changing either file means
new tests and review. Hashes detect changes; they do not authenticate the person
who wrote local files. Keep evidence on the trusted owner's machine.

Use the example's JSON format:

- `name`, `requirements_source` and `expectations_source` explain what is tested
  and where the independently checked answers came from.
- `mode` is `controlled` for fixed local pages, or `live` for a separate, explicit
  website check. CI uses local pages only. Do not combine the two sets of results.
- Each case has a unique `id`, `covers`, typed JSON `inputs`, and `expected`.
  Expected results include **every output, field, row and paging value** in order.
  Expected errors name an exact `category` and `path`. Input-schema refusals use
  category `input`, path `inputs`; they happen before browsing and have no trace.
- Cases are required by default. `required: false` keeps an extra observation
  visible without making it an approval requirement. Optional cases cannot fill
  required coverage. `skip_reason` means untested; `blocked_reason` records a
  reported obstacle, such as no model proposal. Neither counts as an observed
  successful run. Do not remove failed cases from later attempts.
- Cover `demonstration`, `changed_inputs`, `combined_filters`, `zero_results`,
  `invalid_input`, `multiple_pages` and `changed_page`. Each input needs a changed
  value with a different expected answer. Two or more inputs also need a combined
  case. Paging needs a complete result covering at least two pages. Test page
  changes using a controlled fixture, not by editing the candidate mid-run.
- `not_applicable` gives a reason for a requirement that does not apply. Required
  coverage cannot be waived: for example, paging cannot skip the multi-page case.
  The runner can check the declared coverage and values; a reviewer must still
  check that the cases represent the intended task and meaningful filter changes.
- Limits: 100 cases, 1–120 seconds per replay (default 30), and 1–3,600 seconds
  for the whole attempt (`wall_seconds`, default 300). Plans must be valid JSON
  under 2 MiB. Candidates use the existing 256 KiB draft limit. Use public data;
  candidates with marked sensitive inputs or record outputs are refused.

Reports distinguish **passed, failed, blocked and untested** cases. Required
failures or gaps prevent eligibility. A live runtime failure stays blocked even
when an error was expected; an outage or challenge is never a correct data result.
Controlled negative cases may pass their exact refusal check. The runner cannot
diagnose every challenge or outage; unexpected failures retain their actual trace.

## What approval evidence includes

The report records the exact candidate file hash, manifest digest, approved plan
hash, source-code hash, available Git revision and dirty state, runtime versions,
case timings and trace hashes. Browser versions are in each browser trace.
Even a comment edit to a candidate requires a new evaluation.

Model measurements are unknown by default. Optionally pass `--generation-report`
with a saved builder `BuildReport` JSON object. These are imported measurements,
not independently verified charges. Unknown client tokens or costs stay `null`.
Replay makes zero model requests and API charges; local compute cost is unmeasured.

`verify_evaluation(...)` is the shared approval gate for the publishing work in
[#64](https://github.com/browser-to-mcp/browser-to-mcp/issues/64). It rechecks the
candidate, plan, required outcomes and saved trace identities rather than trusting
a green summary. This release does **not** add the published-version library or
change the developer `server --workflow-dir` command. Keeping a build draft still
only keeps a draft. The builder's first-page preview remains separate from these
independent checks.

The two unresolved public-site creation checks in #62 and the full three-site
release trial remain pending. These controlled checks do not complete those gates
or prove that a changing website will keep working.
