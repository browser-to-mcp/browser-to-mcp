# First recorder demo: look up a book

Current implementation status (2026-10-20): recording, guided authoring, and
controlled browser-to-MCP tests are now available. Follow [AUTHORING.md](AUTHORING.md).
The brief below preserves the original plan and its dated observations. The
independent newcomer timing exercise remains pending in #48.

This is the plan for [issue #42](https://github.com/browser-to-mcp/browser-to-mcp/issues/42),
written on 2026-10-19. It gives the later recorder work one clear example to
follow. The full milestone is
[#41](https://github.com/browser-to-mcp/browser-to-mcp/issues/41).

## What we chose

Use [Books to Scrape](https://books.toscrape.com/) to look up a book and return
two things: its title and its displayed price.

This is a public practice website. Its prices are made up. We chose it to test
how someone creates a browser tool, not to check real shop prices.

The future tool will be called `catalog_product_lookup`. An assistant will
give it a book's address code, and the tool will return the two named values.
MCP is the connection that lets an assistant call that tool.

## Who this is for

Our first intended user is a developer who wants an assistant to read product
information from a website.

Today, that person would open a product page, find the title and price, and
copy them into a result. They would repeat those steps while building and
checking their tool. The recorder should let them show the task once and
reuse it for another product.

Our working guess is that they would check the tool after each change or
during a weekly review. We have not measured that frequency or the time saved.
We have not interviewed users or confirmed demand. We have not checked
whether an API would be better for a real shop.

Three kinds of people to ask for feedback later are:

| Person | What we need to learn |
| --- | --- |
| Developer building an assistant tool | Which website task do you repeat, and how often? |
| Person who checks a product catalog | Which fields do you need, and how do you copy them today? |
| Developer who tests browser tools | Can you create this tool without writing page-finding instructions? |

These are people to look for, not customers we have already found. No feedback
has been received, and nobody has been contacted for this ticket.

## What goes in and what comes back

The tool takes one required text input called `product_slug`. This is the
book's address code, such as `a-light-in-the-attic_1000`.

It goes into this fixed address:

```text
https://books.toscrape.com/catalogue/<product_slug>/index.html
```

The tool returns:

| Name | Type | What it means |
| --- | --- | --- |
| `title` | Text | The book's title in the main product area. |
| `price` | Text | The displayed price, including the £ sign. |

Keep the price as text. Do not remove the currency sign or guess a new number.
Both fields must be present and non-empty before the run can be called
successful. Do not return the whole page or prices from other products.

## The three examples

These values were checked on 2026-10-19. The local test page described below
must keep them fixed even if the public website changes later.

| Case | Input | Expected result |
| --- | --- | --- |
| A: a normal lookup | `a-light-in-the-attic_1000` | `title`: `A Light in the Attic`; `price`: `£51.77`. |
| B: another book, with punctuation in its code | `shakespeares-sonnets_989` | `title`: `Shakespeare's Sonnets`; `price`: `£20.66`. |
| C: a book that does not exist | `browser-to-mcp-missing_0` | A failed lookup. No completed book result. The website returned a 404 page. |

The hyphens and underscore in case B must stay part of the address code.
Cases A and B have different titles and prices. Returning the first recorded
book for both inputs must fail the test.

Also check an empty input. It must be rejected before opening a browser.
Other URL characters, such as spaces, `&`, and `#`, need separate tests in
[#43](https://github.com/browser-to-mcp/browser-to-mcp/issues/43). These three
examples do not prove that all URL inputs work.

## What the developer will do

The recorder and guided setup will be built in #46 and #47. They do not exist
yet. This is the agreed journey for that work:

1. Open a fresh browser with no saved login.
2. Open the page for case A.
3. Select the visible book title and call the output `title`.
4. Select its visible price and call the output `price`.
5. Mark the book's address code as the input `product_slug`.
6. Review the browser steps, inputs, outputs, and allowed websites.
7. Save the reviewed workflow.
8. Run it with case B, then call it through the local MCP server.

The developer must be able to select the two page items without writing a
selector. A selector is a saved instruction for finding an item on a page.

For the later implementation, the observed selectors are
`.product_main h1` for the title and `.product_main .price_color` for the
price. Each must point to exactly one item in the main product area.
Missing or unclear matches must be explained, not guessed.

## Which websites are allowed

The trial used only these two allowed hosts:

| Host | Why it is included |
| --- | --- |
| `books.toscrape.com` | The book pages, images, styles, and missing-page response. |
| `ajax.googleapis.com` | The page requests its jQuery script from this host. |

No other supporting hosts were observed in these three page checks. Do not
allow all Google sites or add new hosts automatically. If a later run requests
an unapproved host, keep the existing permission failure and review the change.

The task only opens pages and reads text. Do not use Add to basket or any
shopping control. Downloads, uploads, messages, payments, login, and form
submissions stay disabled. Every step uses `read_only`, `retry: none`, and
`confirmation: never`.

## What a failure must look like

- Empty input: return an `input` error at `inputs.product_slug` before opening
  a browser.
- Missing book: return a failed run. In our trial, the missing title caused a
  `runtime` error at `steps.title`. A generic 404 heading must not become the
  book title.
- Missing or blank title or price: fail the run. Do not return half a result.
- Login, browser challenge, or network problem: keep the failure and its
  reason. Do not solve a challenge or try another account.
- Unapproved website request: keep the `permission` error. It does not prove
  the book is missing.

A trace is the saved record of a run. Failures must keep the existing error
details and trace reference so the developer can see what went wrong.

The trial allowed 30 seconds to open the page, 5 seconds to find each output,
and 60 seconds for the whole run. It used the existing engine and its normal
policy checks. No retries or policy exceptions were added.

## The local test page we need later

Issue #48 will build a small local page with known answers. It must use our own
HTML and local files, not a copy of the shop's code or tracking scripts.

It needs:

- The same `/catalogue/<product_slug>/index.html` address pattern.
- The exact titles and prices from cases A and B, in a main product area.
- Ordinary links to the two books, so a recording can also capture a link click.
- Another product's title and price elsewhere on the page, to catch a wrong
  selection.
- A 404 response for case C, with no book title or price in the product area.
- A slow-page setting that adds the output items after 250 milliseconds.
- A broken-page setting that removes the title item.
- A challenge-page setting with general page text but no product output items.

The test chooses these settings; they are not extra inputs for the public
tool. Only the local test host is allowed. No login or shopping action is needed.

Later tests must record in real Chromium, save and check the workflow, run it
with new input, then list and call its tool through a real local MCP connection.
Run each of A, B, and C ten times and check the exact expected outcome.
Also prove that empty input opens no browser. This repeated test is future
work in #48, not a result claimed by this ticket.

## What we checked for this ticket

Fresh Chromium sessions loaded both book pages without login or a challenge.
Both gave the expected title and price. The missing book returned HTTP 404.

The same cases were then passed to our existing replay engine with the two
allowed hosts above:

| Check | Observed result |
| --- | --- |
| Case A | Completed with the expected title and price. |
| Case B | Completed with the expected title and price. |
| Case C | Failed at the missing title, as expected. |
| Empty input | Rejected by the input check, as expected. |

Each case was checked once in the final replay trial. This shows that the task
is possible; it is not a promise of long-term reliability.

We also checked retail candidates. Adafruit's results included a short-timeout
failure and an unapproved Facebook request. Olimex was refused because it
requested an unapproved Google script. We chose the practice catalog for this
first demo without changing the engine's rules. Those observations do not mean
the other sites can never be supported.

The dated record, browser versions, exact trial workflow, and earlier failures
are in [the evidence file](evidence/pilot-2026-10-19.json). The trial workflow
was kept outside `workflows/`; it has not been added to the server's tool list.

To check the public example by hand, open these pages and compare their main
title and price with the table above:

- [Case A: A Light in the Attic](https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html)
- [Case B: Shakespeare's Sonnets](https://books.toscrape.com/catalogue/shakespeares-sonnets_989/index.html)
- [Case C: missing book](https://books.toscrape.com/catalogue/browser-to-mcp-missing_0/index.html)

The recorder, guided setup, repeatability tests, and new-user timing check
remain separate work in #46–#48. The PyPI investigation in #36 is also separate.
