# Live book journey: evaluated, published and called through MCP

On October 31, a fresh client-mode task created a single-page category-input book
draft on Books to Scrape. Browser exploration and the submitted proposal used
the bounded live DOM. Independent expected answers were grouped by parent IDs,
without running the proposed CSS selectors. Creation used no worker model/key;
the external client's model version, tokens and charges were unavailable.

The same candidate bytes passed all five required checks, were reviewed and
approved in an isolated local library, and were called through a separate STDIO
MCP server with two inputs. The published travel call returned exactly 11 books;
the parenting call returned exactly one book, with every title, price and link
matching the independently saved answers. This is a scoped live success, not a
claim of support for every category or website.

| Case | Source | Result |
| --- | --- | --- |
| Travel category | Live website | All 11 rows matched |
| Parenting category | Live website, changed input | One row matched |
| Missing category | Input validation, no browser | Refused before browsing |
| Empty results area | Synthetic offline HTML | Timed refusal; no data returned |
| Missing price | Synthetic offline HTML | Required field refused; no data returned |

The empty-page refusal takes the candidate's 30-second step deadline because the
draft has no reviewed empty marker. This is conservative failure, not successful
empty-result support. Both synthetic pages are explicitly stored in the pinned
mixed plan; page scripts and outbound requests are disabled for those cases.
Successful live checks and published calls use ordinary browser execution.
The original candidate, selectors, URL and permissions were never edited.

## Evidence and verification

- [Candidate](book.candidate), [proposal](proposal.json), independent
 [travel answers](expected.json) and [parenting answer](changed-expected.json).
- [Evaluation report](evaluation/report.md), [machine report](evaluation/report.json),
 [pinned plan](evaluation/plan.json), [plan hash](plan.sha256), and JSON traces.
- [Published calls](published-calls.json) retain structured outputs, publication
 identity, timings and hashes of the [published traces](published-traces/).
 Redundant MCP text content and local absolute trace addresses are omitted from
 this call summary; the original responses remain in the private run directory.
- [Initial incomplete attempt](initial/report.json) is retained with its plan and
 traces. It was blocked by missing coverage and was never published. The later
 attempt adds those cases without changing the original candidate or answers.

Check the saved bundle without contacting the website:

```sh
.venv/bin/browser-to-mcp check-evaluation \
 docs/evidence/book-journey-2026-10-31/book.candidate \
 docs/evidence/book-journey-2026-10-31/evaluation/plan.json \
 --suite-sha256 sha256:2e6abb3b8d558b6d37f4b940424e767e6805ebf9882cd8562e23539ddb58f724 \
 --evidence-dir docs/evidence/book-journey-2026-10-31/evaluation
```

The report records the exact source hash and truthfully marks the implementation
worktree as dirty at execution time. Failure screenshots and full browser
observations remain private under `traces/live-book-2026-10-31/`; they are not
needed by the publication verifier. The published library is isolated there too.
The saved evidence does not activate a tool in another user's library.

The mixed evaluation took 43.27 seconds. Two normal live evaluations and two
published MCP calls succeeded; there were no blocked or failed live calls in
this attempt. Refusal fixtures are not counted as live successes. Replay made
zero model requests; external client charges and local compute cost are unknown.
Single observations are not a repeated latency benchmark or a human timing.

## Remaining release requirements

Issues #62 and #66 remain open. Laptop and clothing live trials remain unsupported
under the current third-party request policy; see
[site-access-2026-10-31.json](../site-access-2026-10-31.json). The real newcomer
walkthrough (#48), manual comparison, repeated user feedback and full cost gate
still require actual evidence. No user measurements are invented here.
