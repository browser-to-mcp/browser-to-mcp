# travel_books: passed

Candidate: `sha256:c058653232f49bd7c325f407c276b45d5cc7d1436791bea38ede79c500d527ed`
Plan: `sha256:10614aae9f5ad4ef938c9641ce41eac115914d45b5c5a169835930646e1d5c91`
Mode: mixed
Inline fixtures are synthetic offline checks; see the pinned plan and trace fixture hashes.
Elapsed: 36.975 seconds
Tested files: [draft](draft.candidate) and [test plan](plan.json)
Details and runtime versions: [report.json](report.json)

| Case | Check | Observed outcome | Reason | Trace |
| --- | --- | --- | --- | --- |
| travel | passed | outputs | Every expected output value and type matched. | [trace](traces/37a943aa5a6048479cdbd6d0a6ef92a7.json) |
| empty | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/a04cb5f09e30484d831d2e0da5a78dd6.json) |
| missing_price | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/eb995af216a3434686b1c0c057c7318b.json) |

A passed refusal checks safe failure; it is not a successful data result.

Generation usage: see report.json; missing measurements are unknown.
Replay uses no model API. Local compute cost is not measured.

All required checks passed for this candidate and plan. Publishing still needs review.
