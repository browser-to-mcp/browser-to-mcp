# Return product rows as real data

A tool can return one record (`object`) or a list of records (`array`). A record
is a group of named values for one product. For example:

```json
{
  "products": [
    {"title": "Blue Notebook", "price_text": "£12.50", "product_url": "http://127.0.0.1:8766/products/blue?edition=paper"},
    {"title": "Green Notebook", "price_text": "£0.00", "product_url": "http://127.0.0.1:8766/products/green?edition=paper"}
  ]
}
```

These are JSON rows, not JSON packed into a text field. Each field comes from
inside its own row. The tool never reads separate title and price columns and
tries to join them later. It reads the matched rows in one browser snapshot and
keeps their page order.

This implements [#59](https://github.com/browser-to-mcp/browser-to-mcp/issues/59).
It provides extraction from one reviewed results area on one page. Paging and
whole-catalog completeness belong to #60. The [three-site pilot](THREE_SITE_PILOT.md)
still needs its later implementation and real-user validation.

## Try the local example

Use the normal [setup instructions](DEMO.md) first, including Chromium. Start
our original practice pages in one terminal:

```bash
uv run python tests/records/record_site.py --port 8766
```

In another terminal, validate and run the example:

```bash
uv run browser-to-mcp validate examples/records/catalog.yaml
uv run browser-to-mcp replay examples/records/catalog.yaml --input case=normal --trace-dir traces
uv run browser-to-mcp replay examples/records/catalog.yaml --input case=empty --trace-dir traces
```

The first call returns Blue, Green, and Red Notebook, in that order. The second
returns an empty `products` list. Use `case=order` to change the page order,
`case=duplicate` to repeat Blue Notebook, or `case=missing` to remove a required
price and see a failed run. All data is invented for testing.

The example also returns `count` (integer), `amount` (number), `available`
(boolean), and a nullable `note`. These extra fields demonstrate types: the
Blue Notebook has `0`, `0.0`, and `false`, which are valid values. The note is
marked sensitive so traces and previews hide it. The calling client still
receives its actual value. No real credentials or customer data are involved.

To expose this example through local MCP, choose its directory explicitly:

```bash
uv run browser-to-mcp server --workflow-dir examples/records --trace-dir traces
```

It is not added to the default `workflows/` directory. Nothing contacts a public
shop, opens an account, or makes a purchase. Stop the local page server with
Ctrl+C when finished.

## What a record contract looks like

This is the record part of a workflow. The complete runnable contract is in
[the example YAML](../examples/records/catalog.yaml).

```yaml
# Inside steps:
- id: products
  action: extract_records
  selector: "#results"
  record:
    rows: "article.product"
    fields:
      title:
        selector: "a.title"
        source: title
      price_text:
        selector: ".price"
      product_url:
        selector: "a.title"
        source: href
        path_prefix: "/products/"
    duplicates: deduplicate
    unique_by: product_url
    empty_selector: ".empty"
  timeout_seconds: 5
  risk: read_only
  idempotency: safe
  retry: none
  confirmation: never

outputs:
  products:
    type: array
    max_items: 50
    allow_empty: true
    items:
      type: object
      properties:
        title: {type: string}
        price_text: {type: string}
        product_url: {type: string}

success:
- id: products_checked
  assertion: output_matches_schema
  field: products
```

`selector` identifies exactly one visible results area. `rows` selects product
rows inside it. Every field selector is evaluated inside one row. Only fixed
native CSS is supported; templates, XPath, JavaScript expressions, and arbitrary
attributes are not supported. `:scope` can select the area itself as a single
row, or the row itself as a field.

For a single record, use `type: object` with `properties` directly, without
`items`, `max_items`, or `allow_empty: true`. Exactly one row must match. The
runner never silently chooses the first of several products.

## Field values

Each record field uses `string`, `integer`, `number`, or `boolean`. All keys
must be present in the result, and extra keys are refused. Values use the same
[primitive conversion rules](OUTPUTS.md). Numbers must remain finite and retain
their decimal value; `NaN`, infinity, currency strings, and guessed booleans fail.

Fields support three sources:

| Source | What is read |
| --- | --- |
| `text` (default) | Visible HTML text inside the selected element |
| `title` | The element's full `title` attribute, useful when a displayed book name is shortened |
| `href` | A resolved absolute link; requires a reviewed `path_prefix` |

Title attributes and links require string fields. An href must stay on the
current page's exact host and within the declared path prefix. It must use
HTTP(S) and pass the shared secret-URL check. Its query stays intact; its
fragment is removed. Use `/products/`, for example, to reject a cart link.
Reading a link does not click it.

Text and title fields normally trim surrounding whitespace and collapse
internal whitespace to one space. Set `normalize_whitespace: false` on an
extraction field to preserve it. Links always use browser URL rules and keep
query spaces and `+` characters intact; whitespace cleanup cannot be enabled
for an href field. This setting does not change existing scalar extraction.
Prices stay text, including their currency sign and decimals. Do not turn a
shop's price into a number by removing symbols or assume what `$` means.

By default, missing or blank cells fail the whole run. `nullable: true` on an
output field explicitly allows those cases to become JSON `null`. The key is
still required. A literal page word `null` is not automatically converted to
null. Multiple matching cells, a hidden cell, or a field outside its row is an
error even when the field is nullable. Rows are never silently dropped.

## Empty results

An empty list needs all three reviewed declarations:

1. `allow_empty: true` on the array output.
2. An `empty_selector` for exactly one visible empty-results message inside the
   same results area.
3. An `output_matches_schema` success check for that output.

The results area must still exist and be visible. A missing area, generic
verification page, blank layout, or zero rows without the marker fails. Visible
rows together with an empty marker also fail. A single-record output cannot
return an empty record or null. `output_not_empty` rejects an empty list while
still accepting scalar `0`, `0.0`, and `false`.

## Duplicates, order, and limits

Choose the duplicate rule in `record`:

| Rule | Behavior |
| --- | --- |
| `reject` (default) | Fail on a repeated key, or an identical whole record when no key is declared |
| `keep` | Keep every row in page order; do not also declare `unique_by` |
| `deduplicate` | Requires `unique_by`; keep the first identical record for that key and fail if its other values disagree |

A unique key must name a non-nullable field. Two equal titles with different
product URLs stay separate when `product_url` is the key. Duplicate handling
uses the converted values, without mixing rows or reordering the first matches.

The hard limits are:

- 100 matched rows; an array defaults to `max_items: 50` and may choose 1–100.
  This limit applies before deduplication, so duplicate rows cannot bypass it.
- 16 named scalar fields per record; field names have at most 64 characters.
- 4,096 characters per raw cell and per returned string.
- 256 KiB per structured output, measured as UTF-8 JSON including record keys.
- 512 characters per CSS selector and link path prefix.
- One record level: arrays contain records, and records contain scalar or
  explicitly nullable scalar values. Nested arrays and records are refused.

Too many rows or oversized data fail instead of returning a shortened list.
Overlapping row containers and ambiguous results areas also fail. The runner
waits for rows or the approved empty marker within the extraction timeout, then
checks all fields immediately. If a site fills cells after creating its rows,
add a reviewed readiness wait before extraction. The normal overall run limit
still applies, along with the shared [browser restrictions](BROWSER_POLICY.md).

## Create it through guided authoring

Follow [the normal recording and authoring guide](AUTHORING.md). At output
selection, click inside the results area and choose `array` or `object`.
Then:

1. Choose the surrounding area from the numbered choices.
2. Click inside one example product and choose its repeated row container and
   row limit. List authoring needs at least two example rows to check the repeated shape.
3. For each field, choose its name, type, source, null rule, and privacy rule.
   Click that field inside the same example row. You do not type selectors.
4. Choose the duplicate rule. Review the typed preview for all
   matched rows, not just the first one.
5. To allow empty lists, approve the populated preview first. Open an approved
   empty-results state using the browser address bar, then select its message.
   The empty page must retain the same results-area selector. Review the empty
   preview too.
6. Review the full workflow and type `save`. Cancel at any prompt to discard it.

Selection clicks do not activate shopping links or buttons. Ambiguous selections,
fields from another row, unsupported page elements, credential-looking values,
or invalid previews cannot
be exported. A page without stable selectable structure may need a different
results area or a manually reviewed contract. Publication and independent
candidate testing remain separate later work.

## Results, traces, and compatibility

Direct replay, CLI results, and MCP results carry the same native arrays,
objects, numbers, booleans, strings, and nulls. MCP output schemas close every
record to its declared fields and include array and string limits. A run with
an invalid row fails with an output-specific path, such as
`outputs.products.1.price_text`. It returns no completed partial result.

Mark a record field `sensitive: true` to hide its value in saved traces and
authoring previews, including a numeric zero, false, or null. A structured
output can also mark the whole value sensitive. These flags do not redact the
authorized CLI/MCP response. They are declarations, not automatic secret or
personal-data detection. Failure screenshots are omitted when any output field
is marked sensitive.

Structured workflows write trace `schema_version: 2`. Unmarked values retain
their types; marked fields become `[REDACTED]`. Existing scalar workflows keep
trace version 1, their current manifest digests, and their existing text handling.
Older runners and trace readers must be upgraded for these new structures;
unknown actions or schemas should be refused rather than converted to text.
