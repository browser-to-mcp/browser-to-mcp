# Replacement clothing catalog

The owner delegated the replacement choice on November 2. We selected
[Listly's numbered-link fashion tutorial](https://test.listly.io/en/pagination/basic).
Its [introduction](https://test.listly.io/en/) offers collection practice with sample
data. The listing contains clothing, shoes, bags and accessories: 100 records on
ten pages. Only `test.listly.io` is allowed; no tracker, third-party dependency,
login, shopping interaction or browser-policy exception was added.

The original ScrapingCourse [inspection](original-inspection.json) was refused
with `page_blocked_request`. A [Catalog108 candidate](replacement-inspection.json)
was also refused; reviewing its markup identified a tracking script, so it was
rejected rather than allowlisted. A former ScrapingClub practice URL returned 404
and its homepage no longer described a practice catalog. These research reads
were not successful builder attempts. The site choice does not make the original
sites supported.

The first successful Listly inspection [expired while waiting](expired-inspection/final.json).
Its [context](expired-inspection/context.json) and proposal remain available.
A separate [new attempt](listly-inspection-2.json) used a one-hour job wall budget
to allow review; browser actions remained six and each browser step remained
bounded. No automatic retry or deadline extension occurred. That attempt's
[bounded context](context.json), [proposal](proposal.json), [build report](build.json)
and [completion](completed.json) identify the generated candidate, now the
[clothing example](../../../examples/catalog/clothing.yaml). Its selectors were
not manually repaired. Current prices use the emphasized price, excluding any
struck-through original price; product links preserve `?lang=en`.

[Independent answers](expected.json) were collected before evaluation with a
separate HTML parser reading each product's heading, emphasized price and link,
without generated selectors or replay output. The [page observations](independent-pages.json)
retain each page's URL, timestamp, markup hash and exact rows. These reads were
spaced at least one second apart. The site offers collection tutorials; its
extension is an existing collection alternative. This technical exercise does
not establish unmet customer demand or a time advantage over that alternative.

The [initial evaluation](initial-unpaced/evaluation/report.md) matched the data
but revealed that fast paging could exceed the trial's one-second interval.
It remains retained and is not the final acceptance run. The runtime now waits
between page starts within its existing deadline. The [final evaluation](evaluation/report.md)
passed all five checks for the exact same candidate: all 100 products on ten
ordinary live pages, ten products with a one-page limit, invalid input before
browsing, and two explicitly labelled offline refusal pages. Empty markup is a
30-second refusal, not successful empty-list support. A missing required price
fails. Offline fixtures cannot follow Next links or contact the network; they
do not substitute for successful real multi-page evidence.

The tested candidate was explicitly approved only in an isolated local trial
library. A separate STDIO MCP server returned the independently checked results
for default, one-page and twenty-page limits. The [call records](published-calls.json)
and pinned traces retain exact version identity and hashes. Nine invalid or
unavailable requests produced no new replay trace. The saved evidence activates
no tool in a user's library: test, review and publish your own copy explicitly.

Verify the retained final evidence without visiting the website:

```bash
browser-to-mcp check-evaluation examples/catalog/clothing.yaml \
  docs/evidence/shared-clothing-2026-11-02/plan.json \
  --suite-sha256 sha256:9ff992cafc1260f8ac82d2e3ce695ceb651abaa244aa43ae78097a5392cb44e5 \
  --evidence-dir docs/evidence/shared-clothing-2026-11-02/evaluation
```

These are development-worktree observations, not a fixed-release benchmark or
native assistant-client usability study. Creation used the local client-led job
API and normal isolated worker; published invocation used the Python MCP SDK
over STDIO. No worker model or API key was used. External assistant version,
tokens/charges and local-compute cost were not measured. Human onboarding,
manual baseline, repeated use and complete cost evidence remain open under
#62/#66 and #48; this evidence does not close those gates.
