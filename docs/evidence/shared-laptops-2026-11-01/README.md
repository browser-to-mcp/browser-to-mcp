# Replacement laptop catalog

The owner delegated the site choice on November 1. We chose
[TestingURL's laptop practice page](https://testingurl.dev/scraping/ecommerce/category/laptops).
Its [introduction](https://testingurl.dev/) explicitly permits scraping practice.
The original Web Scraper site still refuses third-party requests under the
unchanged policy; [that attempt](original-blocked.json) remains saved.

A TestingURL-only inspection was also [refused](replacement-host-only-blocked.json).
The reviewed workflow adds only its visible font stylesheet/font hosts and
logo host: `fonts.googleapis.com`, `fonts.gstatic.com`, `cdn.geekflare.com`.
The [reviewed inspection](reviewed-inspection.json) succeeded. No tracker was
allowed and the runtime's request, write, redirect and frame rules were unchanged.
The preliminary 20-page task was [cancelled](superseded.json), then replaced by
an honest single-page task. It produced no callable version.

The client-led builder used the [bounded page context](context.json) and
[proposal](proposal.json), previewed all 15 rows, and kept the candidate for review.
The [build report](build.json) records that stage. The generated candidate was
not manually repaired. It is now the [laptop example](../../../examples/catalog/laptops.yaml).
Prices use the reviewed `·` separator to exclude star ratings. New pagination
and changed price formatting cause refusal.

The candidate passed [independent evaluation](evaluation/report.md), was explicitly
approved in an isolated local test library, and was called over a separate STDIO
MCP connection. Default, 1-page and 20-page limits each returned the same 15
products, matching the [independently transcribed answers](expected.json).
The [call records](published-calls.json) retain version identity and trace hashes.
Invalid limits and unavailable catalogs started no replay.

Evaluation combines one ordinary live call with four original offline refusal
pages: empty list, missing price, changed separator and new pagination. Those
fixtures are not live successes. Empty and new-pagination refusals take the
30-second step deadline. The saved evidence does not publish anything in a
user's library; review and publication remain explicit.

Verify it offline (use the hash in `plan.sha256`):

```bash
browser-to-mcp check-evaluation examples/catalog/laptops.yaml \
  docs/evidence/shared-laptops-2026-11-01/plan.json \
  --suite-sha256 sha256:7160082ab8fca3b9d667fedb66922d52cbaa3b934b2dcfac0a435c0639da25d9 \
  --evidence-dir docs/evidence/shared-laptops-2026-11-01/evaluation
```

This changes the laptop pilot's website and single-page scope, not the original
site's supported status. Original local paging checks remain required. Clothing,
manual comparisons, newcomer timing, repeat use and complete cost evidence remain
open. No worker model/key was used; external client tokens/charges and local
compute costs are unknown. These are individual observations, not a benchmark.
