# live_book_categories: passed

Candidate: `sha256:9c9c69acb7afd5bffcfe4e32a352dfb5b3caca45d3fb1eb3029a2c0cc147fbcf`
Plan: `sha256:2e6abb3b8d558b6d37f4b940424e767e6805ebf9882cd8562e23539ddb58f724`
Mode: mixed
Inline fixtures are synthetic offline checks; see the pinned plan and trace fixture hashes.
Elapsed: 43.274 seconds
Tested files: [draft](draft.candidate) and [test plan](plan.json)
Details and runtime versions: [report.json](report.json)

| Case | Check | Observed outcome | Reason | Trace |
| --- | --- | --- | --- | --- |
| travel | passed | outputs | Every expected output value and type matched. | [trace](traces/135b272ae1e44ed880121c35ca556740.json) |
| invalid | passed | error | Typed inputs were rejected before browsing; no trace needed. | — |
| parenting | passed | outputs | Every expected output value and type matched. | [trace](traces/b76b59a024a64d94985d8da47d6fa97a.json) |
| empty | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/3fbed68729dc42d5baa6c5d9847e501b.json) |
| missing_price | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/f156dddb662648289d6eb529443337af.json) |

A passed refusal checks safe failure; it is not a successful data result.

Generation usage: see report.json; missing measurements are unknown.
Replay uses no model API. Local compute cost is not measured.

All required checks passed for this candidate and plan. Publishing still needs review.
