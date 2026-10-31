# live_book_categories: not ready

Candidate: `sha256:9c9c69acb7afd5bffcfe4e32a352dfb5b3caca45d3fb1eb3029a2c0cc147fbcf`
Plan: `sha256:76056e3759b6dac8940fb14995a204b67e7107a8e6be0990ca387eece1b6ee67`
Mode: live
Elapsed: 3.578 seconds
Tested files: [draft](draft.candidate) and [test plan](plan.json)
Details and runtime versions: [report.json](report.json)

| Case | Check | Observed outcome | Reason | Trace |
| --- | --- | --- | --- | --- |
| travel | passed | outputs | Every expected output value and type matched. | [trace](traces/f8370f14c8994b8ead703ef76594f02d.json) |
| invalid | passed | error | Typed inputs were rejected before browsing; no trace needed. | — |

A passed refusal checks safe failure; it is not a successful data result.

Generation usage: see report.json; missing measurements are unknown.
Replay uses no model API. Local compute cost is not measured.

Blocking approval:

- changed_inputs: untested; add a required case or explain why it does not apply.
- zero_results: untested; add a required case or explain why it does not apply.
- changed_page: untested; add a required case or explain why it does not apply.
- changed_inputs: no different input and answer for category.
- zero_results: an empty result or explicit refusal is required.
