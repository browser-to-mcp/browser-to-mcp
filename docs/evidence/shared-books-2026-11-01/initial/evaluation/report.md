# travel_books: passed

Candidate: `sha256:a3ae7244b5d1f981173d961fba99afaf95c69317c8cdcb7d14c865584225dfa2`
Plan: `sha256:10614aae9f5ad4ef938c9641ce41eac115914d45b5c5a169835930646e1d5c91`
Mode: mixed
Inline fixtures are synthetic offline checks; see the pinned plan and trace fixture hashes.
Elapsed: 35.929 seconds
Tested files: [draft](draft.candidate) and [test plan](plan.json)
Details and runtime versions: [report.json](report.json)

| Case | Check | Observed outcome | Reason | Trace |
| --- | --- | --- | --- | --- |
| travel | passed | outputs | Every expected output value and type matched. | [trace](traces/79c074336a574a44a37f3805bdd00444.json) |
| empty | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/6709de324c654c25894af60d27df0da4.json) |
| missing_price | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/78809da2491c415bb14af86f12e4b265.json) |

A passed refusal checks safe failure; it is not a successful data result.

Generation usage: see report.json; missing measurements are unknown.
Replay uses no model API. Local compute cost is not measured.

All required checks passed for this candidate and plan. Publishing still needs review.
