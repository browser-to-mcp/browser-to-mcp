# practice_clothing: passed

Candidate: `sha256:8eea65c2a17c0520bd6af08887c7c440d19de03263f8b3a9c0b7a28f2d41f306`
Plan: `sha256:9ff992cafc1260f8ac82d2e3ce695ceb651abaa244aa43ae78097a5392cb44e5`
Mode: mixed
Inline fixtures are synthetic offline checks; see the pinned plan and trace fixture hashes.
Elapsed: 47.342 seconds
Tested files: [draft](draft.candidate) and [test plan](plan.json)
Details and runtime versions: [report.json](report.json)

| Case | Check | Observed outcome | Reason | Trace |
| --- | --- | --- | --- | --- |
| full | passed | outputs | Every expected output value and type matched. | [trace](traces/2bd97f9cf23a4c03be9465bf499769ec.json) |
| limited | passed | outputs | Every expected output value and type matched. | [trace](traces/44f4826ae4614f7bbd10e40717d6f910.json) |
| invalid | passed | error | Typed inputs were rejected before browsing; no trace needed. | — |
| empty | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/a2e4ef7b9cdb41b5b2643750e9436c44.json) |
| missing_price | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/7e8a694f1e2843f4a8abd8057204a6d4.json) |

A passed refusal checks safe failure; it is not a successful data result.

Generation usage: see report.json; missing measurements are unknown.
Replay uses no model API. Local compute cost is not measured.

All required checks passed for this candidate and plan. Publishing still needs review.
