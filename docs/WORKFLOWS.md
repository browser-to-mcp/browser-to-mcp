# Writing workflow URLs

Use `{{input_name}}` to insert a declared input. Spaces around the name, as in
`{{ query }}`, are allowed. Expressions and filters are not supported.

## Query values

The existing Hacker News and Wikipedia templates keep working without edits:

```yaml
url: "https://hn.algolia.com/?q={{query}}"
```

Pass the original text as input, including any spaces or punctuation:

```bash
uv run browser-to-mcp replay workflows/hn_search.yaml --input 'query=C++ & tools'
```

The URL becomes `https://hn.algolia.com/?q=C%2B%2B%20%26%20tools`. After decoding
the query, the server receives exactly `C++ & tools` as one value. Input cannot
add parameters or a fragment, or select a different website.

Inputs are UTF-8 percent-encoded **once**, as data. Do not pre-encode them:
`%2F` is literal input and becomes `%252F` on the wire; decoding once returns
the three characters `%2F`, not `/`. Spaces become `%20`; literal `+`, `&`,
`#`, and `%` become `%2B`, `%26`, `%23`, and `%25` respectively.

Placeholders may occupy all or part of a query value, may be repeated, and may
use different declared inputs. Fixed URL text is preserved without decoding
or re-encoding, including duplicate parameters, blank values, and fragments:

```yaml
url: "https://hn.algolia.com/?q={{query}}&sort=byDate#results"
```

Any fixed percent escape adjacent to a placeholder must be complete. For
example, use `?q=%25{{query}}` to prefix a literal `%`. Templates such as
`?q=%{{query}}` or `?q=%2{{query}}` are refused: input must not finish an escape
sequence and change what the server decodes.

## Individual path segments

A path placeholder must occupy one whole segment between `/` separators:

```yaml
url: "https://example.com/items/{{item}}/details?lang=en#summary"
```

Declare `item` in `inputs` and `example.com` in `permissions.allowed_domains`.
With input `../a/b?x=1`, the path becomes
`/items/..%2Fa%2Fb%3Fx%3D1/details`. The value occupies one escaped segment;
it cannot add path separators, query parameters, or a fragment. Servers should
split the escaped path into segments before decoding each segment once.

Empty input, `.` and `..` are refused for path placeholders with an `input`
error before browser startup. They cannot represent a nonempty data segment:
browsers normalize dot segments even when their dots are percent-encoded.
These values remain valid in query values when the input's own constraints
allow them. Use another workflow for websites whose routers reinterpret
escaped separators or decode values repeatedly.

## Supported positions and errors

| Position | Supported? |
| --- | --- |
| Query value, such as `?q={{query}}` | Yes |
| Whole path segment, such as `/items/{{item}}/details` | Yes |
| Part of a path segment, such as `/{{item}}.html` | No |
| Scheme, hostname, port, or credentials | No |
| Query parameter name, such as `?{{name}}=value` | No |
| Fragment, such as `#{{section}}` | No |

Unsupported positions and malformed placeholder syntax fail workflow validation
with a `schema` error, before a browser starts or an MCP tool is registered.
URLs must use a fixed, absolute `http://` or `https://` address. Fixed URL text
cannot contain raw whitespace, controls, or backslashes; percent-encode static
characters when needed. Whitespace inside a valid placeholder is allowed.

Every referenced URL input must be declared and provided, including an optional
input if its placeholder is used. Missing or undeclared values produce stable
`input` errors at `inputs.<name>`. All navigation inputs are checked before
browser startup, including those used only in later steps.

An input resembling `https://another.example/path` is still data within its
declared value or segment. The runtime checks the final rendered URL against
the existing scheme and domain policy, and checks browser requests and
redirects against the allowlist as before. No dynamic domains are supported.

## Compatibility

CLI replay and MCP calls share this implementation. Existing query templates
require no syntax changes. Workflows that relied on an input injecting raw URL
syntax must move those fixed separators into the reviewed YAML. Dynamic
authorities, fragments, parameter names, and partial path placeholders are now
rejected explicitly.

Only `goto.url` uses URL encoding. Ordinary `fill` values, selectors, text,
and keys keep their existing plain-text substitution behavior. Passing
`C++ & tools` into a fill action still types exactly that text.

The automated tests use Chromium and a controlled local HTTP server. They
assert the decoded request values and fixed URL structure through both CLI
and MCP calls; they do not measure public-site reliability.
