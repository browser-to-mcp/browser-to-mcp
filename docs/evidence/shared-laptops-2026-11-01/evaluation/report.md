# practice_laptops: passed

Candidate: `sha256:b599fbb0eb4f4fa853a6b5b60b01f6c82ff3b6db05601fe632d58600a25bf303`
Plan: `sha256:7160082ab8fca3b9d667fedb66922d52cbaa3b934b2dcfac0a435c0639da25d9`
Mode: mixed
Inline fixtures are synthetic offline checks; see the pinned plan and trace fixture hashes.
Elapsed: 63.189 seconds
Tested files: [draft](draft.candidate) and [test plan](plan.json)
Details and runtime versions: [report.json](report.json)

| Case | Check | Observed outcome | Reason | Trace |
| --- | --- | --- | --- | --- |
| laptops | passed | outputs | Every expected output value and type matched. | [trace](traces/3c9c093ddf61413bb2bd09fd51d2eb25.json) |
| empty | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/88eb6b00c087457f848c55173c480c9b.json) |
| missing_price | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/afb3eedfeb52424db9a069cea2fb518f.json) |
| changed_separator | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/4e9d4a6640f84c8fae328a3887c3ccf0.json) |
| new_paging | passed | error | The exact required refusal matched; no successful data was returned. | [trace](traces/2c7ebaf9324242dfbd71a9ac8a5eb679.json) |

A passed refusal checks safe failure; it is not a successful data result.

Generation usage: see report.json; missing measurements are unknown.
Replay uses no model API. Local compute cost is not measured.

All required checks passed for this candidate and plan. Publishing still needs review.
