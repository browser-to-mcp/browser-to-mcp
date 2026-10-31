# Three-site catalog pilot

Plan for [#57](https://github.com/browser-to-mcp/browser-to-mcp/issues/57),
2026-10-23. Part of [#56](https://github.com/browser-to-mcp/browser-to-mcp/issues/56).

## Decision

Proceed with a **technical trial**: collect product names, displayed prices,
and product links from a chosen public catalog. Use three practice websites.
Do not call this a proven customer need or a finished product.

Real-user feedback is pending. On 2026-10-23, the project owner said they had no
user task or feedback to supply and asked for the assessment to continue.
No interviews, usage frequency, savings, or willingness to pay have been
measured. No customer outreach has taken place. #57 must stay open until its
real-user work is finished and the decision is reviewed.

The existing [book pilot](PILOT.md) and [journey evidence](JOURNEY_CHECKS.md)
remain separate. This document does not change their results. The building blocks for lists and numbered Next-link paging are described in
[record outputs](RECORD_OUTPUTS.md) and [paging](PAGING.md). The full three-site
tool and caller-supplied `max_pages` contract below still need implementation
and validation. The newcomer timing requirement in #48 is still pending.

## Who might use it, and why

Our proposed user is a person who regularly copies a small public product list
into a sheet or assistant. They would choose a catalog, collect its product
names, prices, and links, then repeat the task when they need fresh information.
This is a hypothesis. Daily or weekly use has not been confirmed.

Compare these options before committing to a real customer pilot:

| Task | Why consider it? | Decision for now |
| --- | --- | --- |
| Public catalog collection | Several practice sites, clear rows, useful paging checks | Choose for the technical trial |
| News headline collection | Easy to inspect, but a published feed may already solve it | Prefer an official feed where available |
| Logged-in order or invoice lookup | Could save repeated work, but needs accounts and private data | Outside this public, read-only release |

Check an official API or export first for each real user's site. An API is a
supported way to request data directly. Use it if it already supplies the needed
fields with suitable access and terms. A browser is useful when a person can
show the task on a page and there is no suitable supported interface. It is not
necessarily the fastest way to read these practice pages.

The site introductions below and targeted searches for their catalog API docs
were reviewed on 2026-10-23. No supported product-data API was identified there
for these three demo catalogs. That is a limited search, not proof none exists.
[Web Scraper's API](https://webscraper.io/documentation/web-scraper-cloud/api)
manages scraping jobs and saved data; it is not a direct catalog data API.
ScrapingCourse uses shop software, but its page alone does not establish a
supported public API. No hidden endpoints were probed. Plain HTML fetching may
be enough for these pages; compare it before making performance claims.

## The three sites

Only these catalog entry points belong to the trial. They are fictional or
practice data, not three customers or three shops selling comparable products.

| Input `catalog` | Start page | Permission evidence and limits |
| --- | --- | --- |
| `books` | [Travel books](https://books.toscrape.com/catalogue/category/books/travel_2/index.html) | [Owner's introduction](https://toscrape.com/) invites scraping practice. Prices are made up. This category is a single-page control. |
| `laptops` | [Laptop listings](https://webscraper.io/test-sites/e-commerce/static/computers/laptops) | [Test-site introduction](https://webscraper.io/test-sites) offers training sites. Use this existing static catalog, not the newer car catalog or login variant. |
| `clothing` | [Practice shop](https://www.scrapingcourse.com/ecommerce/) | [Owner's introduction](https://www.scrapingcourse.com/) offers a product scraping playground. Despite the short input name, this shop also includes accessories. |

Keep the entry URLs fixed in the reviewed workflow. Do not accept an arbitrary
URL. The initial host sets are respectively `books.toscrape.com`,
`webscraper.io`, and `www.scrapingcourse.com`. These are proposed exact hosts,
not a claim that today's domain policy restricts every subdomain or path.
Enforcement improvements belong to #58.

The dated [browser observation](evidence/three-site-2026-10-23.json) records
sample rows, status, browser version, and blocked requests. Only one distinct listing
page per site was sampled. The clothing page first timed out after 30 seconds
waiting for network quiet. A separate follow-up waited for the first product
row and returned 16 rows, with two sample rows recorded. Both attempts remain
in the evidence. The follow-up also blocked a same-host POST request. Supporting hosts were blocked in that observation;
the earlier book pilot separately needed `ajax.googleapis.com`. Do not copy an
observed tracking host into an allowlist. If a required asset is blocked, stop
and review its purpose explicitly. The exploratory probe kept reading after blocking requests; the normal runtime
can refuse those requests. None of these samples certifies normal policy
compatibility. Resolve required dependencies under #58 before a live release.
A successful sample does not prove paging,
all rows, or normal MCP execution works.

Use one browser at a time, no retries, at least one second between listing-page
loads, at most 20 pages, and a 120-second run limit. These are our trial limits,
not published site rate limits. Stop on a login, challenge, 403, 429, network
failure, or unexpected host. Never bypass a challenge or retry through proxies.
Do not click shopping, account, download, or message controls. Shopping actions
can use GET links too, so checking the HTTP method alone is not enough.

## What goes in and comes back

Proposed tool name: `collect_catalog`. Inputs:

| Field | Rule |
| --- | --- |
| `catalog` | Required exact text: `books`, `laptops`, or `clothing` |
| `max_pages` | Optional integer, default 20, from 1 through 20 |

Reject missing/unknown catalogs, blank text, unknown fields, booleans, strings
in place of integers, fractions, and out-of-range page limits before opening a
browser. Do not silently fix spelling or change the catalog.

Return an object with `catalog`, `items`, `pages_read`, `complete`, and
`stop_reason`. Each item has exactly three non-empty strings:

- `title`: the full product name from that row's title attribute or heading.
  Trim surrounding whitespace and collapse internal whitespace to one space.
- `price_text`: the row's current displayed price, with the currency mark and
  decimals kept as shown. Apply the same whitespace cleanup. Do not convert
  currencies or assume `$` means a particular country's currency.
- `product_url`: the product link made absolute using the page URL. Keep its
  query string; remove only the fragment. Reject links outside the reviewed
  catalog's host. Never return an Add to cart link.

A missing, ambiguous, blank, or unreadable required value fails the run; it does
not silently drop that row. Multiple possible current prices require a reviewed
rule before the site can pass. This trial does not extract availability, taxes,
shipping, variants, or product descriptions.

Keep first-seen page order and row order. Use the resulting product URL as the
duplicate key. Identical repeated rows appear once. A repeated URL with different
title or price fails the run as conflicting data. Same title with different
URLs remains two products.

For the public sites, the reviewed listing paths are the travel category's
`index.html` or `page-N.html`, the exact laptop listing path with its `page`
query, and `/ecommerce/` or `/ecommerce/page/N/`. Product links must respectively
match `/catalogue/<slug>/index.html`,
`/test-sites/e-commerce/static/product/<id>`, or `/ecommerce/product/<slug>/`.
Reject cart/action query parameters and unexpected query parameters; review
any changed URL shape before allowing it. For local fixtures, allow only the
specified listing paths and `/products/<slug>` product links.

Follow the catalog's next-page link only. Verify that it stays in the reviewed
listing path. A page counts after its rows have been fully checked. No next link
means `complete: true`, `stop_reason: "end"`. Reaching `max_pages` while a next
link exists means `complete: false`, `stop_reason: "page_limit"`. A link to an
already visited page is a paging failure, not completion. Do not invent a next
URL or count an unrelated link as paging.

A clearly marked empty catalog returns `items: []`, `pages_read: 1`,
`complete: true`, `stop_reason: "end"`. Zero matched rows without an explicit
empty-state message is a failure. A 404, challenge, broken page, timeout, or
policy refusal returns a failed run with its reason and trace, not an empty or
partly successful catalog. Any partial diagnostic rows must stay outside the
successful tool result. The planned error names below are requirements, not
new error codes already supported by the engine.

For live examples, the evidence file contains exact first-row title, price,
and URL triples for each site. Those are dated samples, not a frozen expectation
for the whole changing website. For complete, fixed expected answers, use the
local specification below.

## Local pages and exact answers

Build original HTML and local assets under a new test fixture. Do not copy shop
HTML, scripts, images, or the existing #48 fixture. Use a random loopback port,
represented here by `{base}`. Tests substitute the actual base URL.

Provide three layouts: `/books/` uses cards; `/laptops/` uses table rows;
`/clothing/` uses cards inserted after 250 ms. Each layout has the same invented
data and ordinary next links. A page reload must reset the data. Put a fake
sidebar product and cart total outside the collection to catch wrong selection.

| Page | Product URL suffix | Title | Price text |
| --- | --- | --- | --- |
| 1 | `/products/blue` | Blue Notebook | £12.50 |
| 1 | `/products/green` | Green Notebook | £0.00 |
| 2 | `/products/green` | Green Notebook | £0.00 |
| 2 | `/products/red` | Red Notebook | £9.75 |

Page 1's next link points to `/{catalog}/?page=2`; page 2 has no next link.
Every title has whitespace around it in HTML to exercise cleanup. Product
links are relative `/products/...` addresses. The three resulting unique rows
are blue, green, red, in that order. The zero price is valid. Use these fixed
cases for **each** layout:

| Case | Input or local setting | Exact expected outcome |
| --- | --- | --- |
| Full | `catalog` set to this layout, default page limit | Three rows above, absolute URLs, `pages_read: 2`, `complete: true`, `stop_reason: "end"` |
| Limited | Same catalog, `max_pages: 1` | Blue and green only, `pages_read: 1`, `complete: false`, `stop_reason: "page_limit"` |
| Empty | Explicit empty-state mode, default limit | Empty list, one page, complete/end |
| Broken | Remove red's price on page 2 | Failed run for missing price; no successful result |
| Conflicting duplicate | Change green's page-2 price to £1.00 | Failed run for conflicting duplicate |
| Paging loop | Page 2 links back to page 1 | Failed run for repeated page |
| Wrong destination | Next link points to a second, unapproved local server | Policy failure; second server receives no request |
| Missing page | Page 2 returns HTTP 404 | Failed run; not complete and not empty |
| Challenge | Replace page 2 with a generic verification page | Failed run; no attempt to solve it |
| Slow beyond limit | Delay content beyond the configured run limit | Timeout and browser cleanup |
| Invalid input | Missing catalog; unknown catalog; `max_pages` = 0, 21, true, 1.5, or "2" | Input failure, no browser or HTTP request |

Modes are fixture controls, not public tool inputs. Normal pages must not make
external requests. Tests should also remove the empty-state marker, leave a
price blank, supply a cart link as a product URL, and give two distinct URLs the
same title. Expected results are respectively failure, failure, failure, and
two retained rows. The test author fixes expectations independently of any
workflow builder; a builder cannot change them to make its output pass.

## How we will measure it

Set these targets before building or scoring the new flow. They are proposed
release gates, not results already achieved.

| Measure | Target and method |
| --- | --- |
| Creation success | At least 8 of 9 attempts: three separately started builds per layout. A success needs a reviewed tool that passes fixed full, limited, and empty cases without hand-editing generated code. Count every started attempt, including timeouts and cancellations. |
| Correctness | All 90 controlled calls pass: three layouts × full/limited/empty × ten runs. Compare every field, row order, completeness, and count with this specification. All listed refusal cases must also pass separately. |
| First correct result | At least 8 of 9 build attempts finish within 10 minutes from submitting the task to the first verified MCP result. Keep failed attempts in the denominator. Measure installation separately. Report automated and human timings separately. |
| Run latency | For the 90 controlled calls, report median and nearest-rank p95; target p95 at most 10 seconds. Time from MCP request to reply, include browser startup, record machine and versions. Failures remain correctness failures and have separate elapsed times. Live-site timings are a separate report. |
| Cost | At most US$1 of model/API charges per build attempt and US$0.01 per replay. Count failed attempts and retries; log provider/model, token usage, dated rates, and charges. Include local browser seconds separately. If no billable API is used, say so; do not call electricity or hosting free. Unknown costs mean this gate is unmeasured. |
| Repeat use | After finding up to three willing users, at least two of three use the tool for their own task on three distinct days within 14 days. With fewer than three participants this gate stays pending. Practice runs do not count as user demand. |

Manual baseline, before the user tries the tool: open the chosen catalog, copy
all names/prices/product links into a sheet, visit each next page, remove exact
duplicates, then compare every cell against the pages. Start the timer at opening
the catalog and stop after checking the sheet. Record pages, rows, errors, total
time, interruptions, and help received. No baseline time has been measured.
Use the same fixed pages for a fair software comparison; keep real-user tasks
and changing live sites in a separate record.

For each willing user, collect consent to record feedback, date, role (or an
agreed anonymous label), exact task, current steps, actual frequency, required
fields, mistakes and their consequences, preferred API/export alternatives,
help needed, and measured timings. Separate their words from interpretations.
Do not publish identities or private catalog data without permission.

## What happens next

The [October 31 book journey](evidence/book-journey-2026-10-31/README.md)
records a live single-page category draft, independent checks, local publication
and two successful MCP calls with different inputs. Empty and changed-page checks
use labelled offline fixtures. Laptop/clothing live support, human measurements
and the remaining release gates are still pending; this does not close #66.

Proceed with #58's restrictions and #59–#60's list and paging work using the
controlled specification. Recheck public permissions and page structure before
each opt-in live trial. Keep live traffic out of CI. A live-site result needs
independent comparison against that day's page data, not these historical samples.

Change the chosen customer task if interviews show different required fields,
low repetition, or a better supported API. Stop expansion if access needs login,
permission is unclear, correctness misses the gate, or actual users do not
repeat the task. Fix the cause and explicitly revise the plan before trying a
new evaluation; do not lower targets after seeing results.

To finish #57: obtain real user evidence through an authorized channel, measure
the manual baseline, and review whether this technical task is worth a customer
pilot. Until then, product demand and time saved remain unknown.
