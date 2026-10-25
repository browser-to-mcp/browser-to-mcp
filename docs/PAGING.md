# Read more than one results page

Some websites show only a few products at a time. Paging lets one reviewed
workflow follow the site's Next link and collect the rest.

The result includes the products and a small summary:

```json
{
  "products": [
    {"title": "Blue Notebook", "price_text": "£12.50", "product_url": "http://127.0.0.1:8767/products/blue"},
    {"title": "Green Notebook", "price_text": "£0.00", "product_url": "http://127.0.0.1:8767/products/green"},
    {"title": "Red Notebook", "price_text": "£9.75", "product_url": "http://127.0.0.1:8767/products/red"}
  ],
  "collection": {"pages_read": 2, "complete": true, "stop_reason": "end"}
}
```

Always check `complete`. A successful call can reach a limit before it reaches
the last page. In that case, it returns the rows collected so far and sets
`complete` to `false`. A broken page, timeout, or cancelled call fails and
returns no product list.

This implements [#60](https://github.com/browser-to-mcp/browser-to-mcp/issues/60).
It adds paging to the existing [record output](RECORD_OUTPUTS.md) action.

## Try it locally

Follow [the setup guide](DEMO.md), including installing Chromium. In one
terminal, start the original practice website:

```bash
uv run python tests/paging/paging_site.py --port 8767
```

In another terminal:

```bash
uv run browser-to-mcp validate examples/paging/catalog.yaml
uv run browser-to-mcp replay examples/paging/catalog.yaml --input case=full --trace-dir traces
uv run browser-to-mcp replay examples/paging/catalog.yaml --input case=empty --trace-dir traces
uv run browser-to-mcp replay examples/paging/catalog.yaml --input case=conflict --trace-dir traces
```

The first call reads two pages. Green Notebook appears on both pages but is
returned once. The second returns an empty list with `complete: true`. The
third fails because the same product has different prices on the two pages.
The products and prices are invented test data.

To try a limit, copy the example outside `workflows/`, change
`paging.max_pages` to `1`, validate the copy, and run it with `case=full`.
It returns Blue and Green Notebook with `complete: false` and
`stop_reason: page_limit`. A changed workflow receives a different manifest digest.

To offer the example through local MCP:

```bash
uv run browser-to-mcp server --workflow-dir examples/paging --trace-dir traces
```

The example is outside the default tools directory. Stop the practice website
with Ctrl+C when finished.

## Review the paging rules

Start with a working `extract_records` step and an array output. Add this
beside the step's `record` settings:

```yaml
paging:
  next_selector: "a.next"
  end_selector: ".end"
  page_parameter: page
  max_pages: 20
  timeout_seconds: 60
  status_output: collection
```

Then declare the summary output:

```yaml
outputs:
  # Keep the existing products array here too.
  collection:
    type: object
    properties:
      pages_read: {type: integer}
      complete: {type: boolean}
      stop_reason: {type: string}
```

The paging step produces this summary itself. Do not add another extraction
step for `collection`. Each paging step needs its own summary name. The loader
checks this before opening a browser.

The complete example is [catalog.yaml](../examples/paging/catalog.yaml).
Paging is configured in reviewed YAML. The guided authoring prompts currently
create single-page record extraction; they do not select or preview paging rules.

### Next links and the last page

Both selectors use fixed native CSS across the whole document.

- `next_selector` must identify exactly one visible ordinary `<a href>` link.
  Download links, links opening another tab, and disabled links fail.
- `end_selector` must identify exactly one visible marker that means there are
  no further pages. Review this marker on the real final page.
- A Next link and an end marker together are an error. Several matches are also
  an error. A missing Next link alone does not prove completion.

If the last page uses a disabled Next link as its end marker, choose a Next
selector that excludes that disabled element and an end selector that matches
it. For example, `a.next:not([aria-disabled="true"])` and
`a.next[aria-disabled="true"]`, when those attributes match the reviewed site.

The runner reads rows and the paging controls together in one browser snapshot.
It waits for rows or the approved empty marker, plus a paging control, then
checks the fields immediately. It follows the validated link address directly;
it does not run the link's click handler. JavaScript-only buttons, Load More,
infinite scrolling, cursor tokens, and arbitrary loops are unsupported.

### Page addresses and filters

Choose exactly one address style:

| Setting | Supported example |
| --- | --- |
| `page_parameter: page` | `/catalog?category=books&page=2`, then `page=3` |
| `page_path: "/catalog/page/{page}/"` | `/catalog/page/2/`, then `/catalog/page/3/` |

The first page must be opened by the reviewed workflow. For query paging, its
page parameter must be absent or exactly `1`. For path paging, the initial
reviewed address may omit a page number. Both styles expect page 2 next, then
3, and so on. Only `{page}` is supported in `page_path`.

The Next link must actually exist. The runner checks its address against the
expected number; it never invents and visits an address because the link is
missing. The scheme, hostname, and port must stay exactly the same. All original
query names and values must stay the same, except the declared page parameter.
Query ordering may change. Duplicate values are preserved and checked. A
fragment, changed filter, skipped page number, or different path fails before
navigation. These checks verify the address; they cannot prove that a website
applied a filter correctly to its data. Use known expected results when testing
a workflow.

The shared [browser rules](BROWSER_POLICY.md) apply to every request. HTTP
redirects remain refused, including redirects to another allowed address.
A non-success HTTP response fails the run. No additional hosts or write
permissions are granted by paging.

## Duplicates and order

Paging requires `duplicates: deduplicate` and a non-nullable `unique_by` field
in `record`. For a product list, use a stable product URL rather than its title.

Rows retain their first-seen order, page by page. An identical repeated product
is kept once. The same identity with different values fails the whole call,
including conflicts later on the same page. A repeated set of rows also fails,
even when the page address or row order changes. A Next link pointing backwards
or staying on the current page fails the address check.

For an empty collection, the first page needs both the approved empty-results
marker and the end marker. Keep `allow_empty: true` and its explicit schema
success check from the record contract. An empty later page is a failure; it is
not accepted as proof that the previous page was the last one.

## Limits and outcomes

| Rule | Meaning |
| --- | --- |
| `paging.max_pages` | Required, from 1 to 20 pages per collection |
| Array `max_items` | From 1 to 100 unique returned records; defaults to 50 |
| `paging.timeout_seconds` | Required, from 1 to 300 seconds for the whole collection |
| Step `timeout_seconds` | Bounds each navigation and readiness wait |
| Overall replay timeout | Still bounds the whole workflow, including browser startup |

The page and record limits are fixed in the reviewed workflow. There is no
caller-supplied limit or resume token in this release. Each call starts afresh;
concurrent calls have separate browsers and collection state.

`stop_reason` is one of:

- `end`: the page has the reviewed end marker, every row fits, and all checks
  pass. `complete` is `true`.
- `page_limit`: another valid Next link exists, but the page budget has been
  reached. `complete` is `false`.
- `record_limit`: the result reached its record budget while more rows or
  another page remain. `complete` is `false`.

An end marker takes priority over a limit when every row on that final page
fits. If the last page contains more records than the budget, only the first
records that fit are returned, with `record_limit`. Rows on each visited page
are validated before this shortening happens. Unvisited pages are not checked.

Separately, each page may contain at most 100 matched rows before duplicate
removal. The existing 16-field, 4,096-character cell, and 256 KiB result limits
still apply. Overlarge pages, invalid rows, conflicting duplicates, or an
oversized combined result fail. These are data errors, not successful limited
collections. There are no retries.

A timeout, cancellation, broken Next control, refused request, or failed page
returns no partial product result. The error or failed trace makes clear that
collection is incomplete.

## Traces and verification

Paging workflows write trace version 3. Each paging action records the page
number, row count, collected record count, duration, and outcome. Failed and
cancelled runs retain earlier page observations. These page observations do
not contain addresses, product identities, or cell values. Successful outputs
still follow the existing field and input redaction rules. Failure screenshots
remain disabled for sensitive output fields and browser-policy failures.

Single-page records still use trace version 2. Scalar workflows still use
version 1. Existing workflows without paging keep their manifest digests.
Upgrade older readers before using paging traces.

The checks in `tests/paging/` use original local pages with cards, tables, and
delayed content. They exercise ordinary CLI and MCP results, both address
styles, duplicate rows, limits, failures, cancellation, and separate concurrent
calls. Run them with:

```bash
uv run pytest -q tests/paging
```

These checks make no public-site requests. They do not claim the three-site
pilot is deployed, that a real user finished a timed walkthrough, or that
customer demand has been validated. Those requirements remain separate.
