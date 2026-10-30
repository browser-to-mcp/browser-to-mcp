# Check the full journey

After the [usual setup](DEMO.md), run:

```bash
BROWSER_TO_MCP_RELEASE_REPORT=reports/release.json \
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

The [dated results](evidence/release-2026-10-30.json) separate these checks from
live websites and real people. Fixed replies do not measure model quality.
Timings start at submitting a task or calling a tool; installation is separate.
Each saved version must pass its own independent tests before publication.
The controlled correctness, creation, first-result timing and call-latency gates
are enforced by pytest. A missed gate fails the run after saving its report,
so the evidence remains available even when CI fails.

These checks use one search tool per layout (`query` and optional `max_pages`).
They exercise the pilot's fixed notebook answers; the proposed single
`collect_catalog(catalog, max_pages)` interface is still unverified.

The live trial read the book category; the other two sites returned
`page_unavailable`. Stop on that error and review access and required hosts;
do not bypass it. No live three-site success is claimed.

#66 stays open for the remaining live coverage, actual model/client costs,
manual comparison, first-time walkthrough (#48), and repeated user feedback.
The owner confirmed on October 30 that user results are not yet available.
Continue the local technical trial; hold the release and wider expansion.
