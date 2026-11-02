# practice_clothing: passed

Candidate: `sha256:8eea65c2a17c0520bd6af08887c7c440d19de03263f8b3a9c0b7a28f2d41f306`
Plan: `sha256:9ff992cafc1260f8ac82d2e3ce695ceb651abaa244aa43ae78097a5392cb44e5`
Mode: mixed
Inline fixtures are synthetic offline checks; see the pinned plan and trace fixture hashes.
Elapsed: 47.828 seconds
Tested files: [draft](draft.candidate) and [test plan](plan.json)
Details and runtime versions: [report.json](report.json)

| Case | Check | Observed outcome | Reason | Trace |
| --- | --- | --- | --- | --- |
| full | passed | outputs | Every expected output value and type matched. | [trace](traces/550c1384bd164858bec3f8d52dcbbac7.json) |
| limited | passed | outputs | Every expected output value and type matched. | [trace](traces/fd662a041bb143668bdad31a8c476c2e.json) |
| invalid | passed | error | Typed inputs were rejected before browsing; no trace needed. | — |
| empty | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/49aea154a26b4229ae7a61acf21a1965.json) |
| missing_price | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/7ba596e51fcb4b46aa8a95306959d829.json) |

A passed refusal checks safe failure; it is not a successful data result.

Generation usage: see report.json; missing measurements are unknown.
Replay uses no model API. Local compute cost is not measured.

All required checks passed for this candidate and plan. Publishing still needs review.
