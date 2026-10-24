# Named and typed browser outputs

All fields in `outputs` are required. Each field must have a producing step
with the same ID. An `extract_text` step can produce any supported primitive;
a permission-gated `download` step can produce a string filename only.
An `extract_records` step can produce a bounded object or array; see
[record outputs](RECORD_OUTPUTS.md). Every record extraction requires a declared
output. Scalar extraction steps whose IDs are not declared outputs do not add result fields.
There is no separate mapping expression or transformation language.

Missing producers, unsupported producer actions, invalid output names, and
success checks referring to undeclared outputs fail with `schema` errors
before browser startup or MCP registration. Output names use public Python
identifiers and must not conflict with the generated schema model API.

## Conversion rules

Primitive browser extraction returns text. The shared replay engine converts and
validates all declared outputs before checking success or reporting completion.
CLI and MCP use this same path.

| Declared type | Accepted extracted text | Returned JSON |
| --- | --- | --- |
| `string` | Non-blank text, preserved exactly as extracted | `"  Copper kettle  "` |
| `integer` | JSON integer literals, such as `12`, `-12`, or `0` | `12` |
| `number` | Finite JSON numbers, such as `19.99`, `12`, or `1.25e2` | `19.99`, `12.0`, or `125.0` |
| `boolean` | Exact JSON literals `true` and `false` | `true` or `false` |

For non-string types, surrounding JSON whitespace (space, tab, carriage return,
and newline) is allowed. It is not removed from declared strings. Blank or
whitespace-only strings fail because outputs are required.

Integers remain exact integers; decimal and exponent notation such as `12.0`
or `1e2` is not accepted for an `integer`. Numbers use finite Python floats.
The numeric JSON value must survive conversion and serialization without
decimal precision loss. Overflow, underflow to zero, and silently rounded
values are refused; for example, use an `integer` declaration for the exact
integer `9007199254740993` rather than a `number`. Standard values such as
`19.99` remain supported.

There is no currency, locale, or availability guessing: `$19.99`, `1,234`,
`available`, `True`, quoted JSON strings such as `"12"` for an integer,
`NaN`, and `Infinity` do not become numeric or boolean values. Declare such
page text as strings or report a conversion failure. Bounded flat records and
arrays of records use the separate `extract_records` action. Nested records,
dates, and general transformations remain unsupported.

## Multiple fields in one workflow

This illustrative page has four independent text elements. Replace the URL
and selectors with the reviewed page's actual values before using it.

```yaml
name: catalog_lookup
description: Return the title, stock, price, and availability shown on a reviewed page.
version: 1
risk: read_only
manifest:
  version: 1
permissions:
  allowed_domains: [example.com]
inputs:
  item:
    type: string
steps:
  - id: open
    action: goto
    url: "https://example.com/catalog/{{item}}"
    timeout_seconds: 30
    risk: read_only
    idempotency: safe
    retry: none
    confirmation: never
  - id: title
    action: extract_text
    selector: "#product-title"
    timeout_seconds: 30
    risk: read_only
    idempotency: safe
    retry: none
    confirmation: never
  - id: stock_count
    action: extract_text
    selector: "#stock-count"
    timeout_seconds: 30
    risk: read_only
    idempotency: safe
    retry: none
    confirmation: never
  - id: price
    action: extract_text
    selector: "#price"
    timeout_seconds: 30
    risk: read_only
    idempotency: safe
    retry: none
    confirmation: never
  - id: available
    action: extract_text
    selector: "#available"
    timeout_seconds: 30
    risk: read_only
    idempotency: safe
    retry: none
    confirmation: never
outputs:
  title:
    type: string
  stock_count:
    type: integer
  price:
    type: number
  available:
    type: boolean
success:
  - id: stock_present
    assertion: output_not_empty
    field: stock_count
  - id: availability_present
    assertion: output_not_empty
    field: available
```

If the page shows `Copper kettle`, `0`, `19.99`, and `false`, the result includes:

```json
{"outputs": {"title": "Copper kettle", "stock_count": 0, "price": 19.99, "available": false}}
```

`output_not_empty` checks presence, not truthiness: valid `0`, `0.0`, and
`false` pass. An absent or blank string fails. An application that needs to
distinguish an unavailable product or no result must still declare the relevant
fields and reviewed success criteria; primitive conversion does not establish
that a lookup was semantically successful.

## Failures and traces

An absent extracted field, blank required string, or invalid conversion uses
the existing `assertion` category and an output-specific path, for example:

```text
assertion: outputs.stock_count: Output must contain a JSON integer.
```

CLI reports a failed run (exit code 1); MCP returns `isError: true` with the
same category, path, and message. Both include a failed trace reference when
tracing is enabled. Invalid page values are not echoed in conversion errors.
The browser actions may have completed even though final output validation
failed; the trace status is still `failed`. A selector or browser failure keeps
its existing `runtime` category and step path.

Successful replay results, MCP results, and JSON traces preserve the declared
primitive types. Sensitive string values are recursively redacted from saved
inputs and outputs, including encoded URL text. Numeric and boolean output
values keep their types. A redacted string is replaced with `[REDACTED]` in
the trace; the authorized caller still receives the original successful output.
Errors are redacted before CLI or MCP presentation. Failure screenshots are
omitted when a provided input or structured output field is marked sensitive,
because structured-text redaction cannot hide data inside an image. Browser-boundary
failures also omit screenshots. Other failed runs keep the existing screenshot behavior.

The HN and Wikipedia workflows continue to return text without conversion or
trimming. Workflows that declared a field without a producer, or mapped an
output to a navigation or input action, must fix that declaration before they
can run.
