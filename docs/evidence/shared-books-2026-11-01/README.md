# Shared book catalog: live check

The fixed [travel workflow](../../../examples/catalog/books.yaml) passed independent
checks, was explicitly approved in an isolated local library, and was called
through a separate STDIO MCP server as `collect_catalog`.

All 11 titles, prices and links matched the independently saved October 31
answers. Default, 1-page and 20-page limits each returned the same complete
single page. Invalid limits and an unavailable catalog were rejected without
starting replay. See [the calls](published-calls.json) and their pinned traces.

The workflow is an explicitly adapted example, not a new model-generated draft.
Its URL is fixed to the travel category. A new Next link makes extraction fail
until the workflow is reviewed again; the local regression tests check this.
The empty-page and missing-price evaluation cases use offline synthetic HTML.
An unmarked empty page is a timed refusal, not successful empty-list support.

The [evaluation](evaluation/report.md) and [plan](plan.json) retain exact answers,
failures and source identity. The [initial attempt](initial/evaluation/report.md)
also passed and remains saved; the final candidate adds the Next-link guard
and was evaluated and published separately. No attempts were discarded.
Publication applies only to the private test library under `traces/`; users
must test, review and publish their own copy. This evidence activates no tools.

Verify the final saved evidence without visiting the website:

```bash
browser-to-mcp check-evaluation examples/catalog/books.yaml \
  docs/evidence/shared-books-2026-11-01/plan.json \
  --suite-sha256 sha256:10614aae9f5ad4ef938c9641ce41eac115914d45b5c5a169835930646e1d5c91 \
  --evidence-dir docs/evidence/shared-books-2026-11-01/evaluation
```

These are individual software observations, not a performance or human timing
study. No model calls were needed for replay; external client charges and local
compute cost were not measured. Laptop/clothing support and user evidence remain
open under #62/#66 and #48.
