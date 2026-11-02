# Check the full journey

After the [usual setup](DEMO.md), run:

```bash
BROWSER_TO_MCP_RELEASE_REPORT=reports/release.json \
  BROWSER_TO_MCP_CATALOG_REPORT=reports/catalog.json \
  uv run pytest tests/release -q --junitxml=reports/release.xml
```

Use a new report name for each run. Earlier attempts stay available for review.
CI saves the report, exact drafts, expected answers, test results and run traces
for fourteen days, including failed runs.

The check creates a tool three times for each local layout: book cards, a laptop
table and delayed clothing cards. It uses fixed assistant replies, real Chromium,
the normal review and publishing steps, and a separate MCP server. It then checks
90 calls: full lists, shorter lists and empty results, ten times per layout.
It also checks bad inputs, broken pages, cancellation, rollback and retirement.
A separate shared-catalog journey builds nine fixed catalogs and checks another
90 calls through `collect_catalog`, with full, limited and empty outcomes, exact
trace/publication identity and preflight refusals. Both reports enforce the same
controlled creation, correctness and timing gates and retain failed attempts.
The [November 2 shared-catalog report](evidence/shared-catalog-2026-11-02.json)
records 9/9 builds and 90/90 exact outcomes. Its fixed replies and original local
pages measure software behavior, not live model quality or human usability.

The [dated results](evidence/release-2026-10-30.json) separate these checks from
live websites and real people. Fixed replies do not measure model quality.
Timings start at submitting a task or calling a tool; installation is separate.
Each saved version must pass its own independent tests before publication.
The controlled correctness, creation, first-result timing and call-latency gates
are enforced by pytest. A missed gate fails the run after saving its report,
so the evidence remains available even when CI fails.

These checks use one search tool per layout (`query` and optional `max_pages`).
They exercise the pilot's fixed notebook answers. Separate
`tests/builder/test_shared_catalog.py` checks cover the [shared catalog tool](CATALOG.md):
all three layouts, page limits, failures, invalid inputs, and a published STDIO call.

The October 30 live trial read the book category; the other two sites returned
`page_unavailable`. Stop on that error and review access and required hosts;
do not bypass it. No live three-site success is claimed.
The [October 31 follow-up](evidence/site-access-2026-10-31.json) identified
unapproved third-party requests on both sites. The builder now preserves the
safe `page_blocked_request` reason. The host lists and fail-closed policy are
unchanged, so those original sites remain blocked. Later reviewed replacements
provide [laptop](evidence/shared-laptops-2026-11-01/README.md) and
[clothing](evidence/shared-clothing-2026-11-02/README.md) evidence, alongside the
[shared book](evidence/shared-books-2026-11-01/README.md) checks. Clothing includes
100 independently checked products across ten live pages. This revises the
supported entry points; it does not repair the original blocked sites.

#66 stays open for remaining actual model/client coverage and costs,
manual comparison, first-time walkthrough (#48), and repeated user feedback.
The owner confirmed on October 30 that user results are not yet available.
Continue the local technical trial; hold the release and wider expansion.
