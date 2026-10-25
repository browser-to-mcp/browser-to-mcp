# Standalone draft creation

Use this path when our worker should call a model itself. To use the AI already
inside Codex/Claude instead, follow the [main guide](TASK_BUILDER.md).

## OpenAI API

Choose an API model that supports Responses API JSON output. Configure the
worker using your normal secret manager; never put a key in task files, model
prompts, chat answers, workflow files, or command arguments.

Set these environment variables before running `uv run browser-to-mcp jobs worker`:

| Variable | What to supply |
| --- | --- |
| `BROWSER_TO_MCP_PROVIDER` | `openai` |
| `OPENAI_API_KEY` | Your API key, supplied privately |
| `BROWSER_TO_MCP_OPENAI_MODEL` | The exact model ID you want to use |
| `BROWSER_TO_MCP_INPUT_MICROUSD_PER_MILLION` | An upper-bound input price, in micro-US dollars per million tokens |
| `BROWSER_TO_MCP_OUTPUT_MICROUSD_PER_MILLION` | An upper-bound output price in the same units |
| `BROWSER_TO_MCP_MODEL_MAX_TOKENS` | Optional output limit, 128–16,384; default 4,096 |

One US dollar is 1,000,000 micro-US dollars. For example, a hypothetical price of
US$2 per million tokens becomes `2000000`. This is a conversion example, not a
current model price. Check your model's [current pricing](https://developers.openai.com/api/docs/pricing)
and account terms; configure rates that cover all input/output tokens. Cached
input discounts are ignored, so estimates stay conservative. Update your settings
when prices change. No API request starts just because a key exists: provider
mode and the worker's provider setting must both be selected.

The adapter uses the [Responses API](https://developers.openai.com/api/docs/guides/text)
and its [input-token counter](https://developers.openai.com/api/docs/guides/token-counting).
It sends the same model, prompt and JSON-output format to the counter and generation
request. Before inference, the job reserves input tokens plus the allowed output
tokens and the corresponding configured cost. Counting still contacts OpenAI
and shares the public task/page data. The adapter requests JSON; our normal closed
proposal schema and policy checks decide whether that JSON is acceptable.

Generation sets `store: false`, requests no model tools, and uses a fixed HTTPS
OpenAI endpoint. It makes no automatic retry, follows no redirect, and ignores
environment proxy settings. Incomplete output, refusals, unexpected tool calls,
invalid JSON/proposals, and inconsistent usage fail safely. `store: false` is not
a promise of zero provider retention; your account's provider policies apply.

The report records the configured model, actual returned token counts, request
duration, and a cost estimate using the configured rates. `api_cost_microusd` is
unknown because the app does not read your invoice. The rate values used are saved
with the report; job timestamps identify when those assumptions applied.
The estimate and reserved
budget are not certified billing totals. Failed calls can still incur charges;
reserved limits are not refunded. API credentials never enter job metadata.
Other hosted providers need their own adapter; they cannot be enabled just by
replacing the API URL.

## Optional local model

Run a [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
with a compatible model file you already have permission to use:

```bash
llama-server -m /absolute/path/to/model.gguf \
  --host 127.0.0.1 --port 8080 --ctx-size 32768 --parallel 1 --log-disable
```

Set the worker environment:

```bash
export BROWSER_TO_MCP_PROVIDER=llama_cpp
export BROWSER_TO_MCP_MODEL_URL=http://127.0.0.1:8080
export BROWSER_TO_MCP_MODEL_MAX_TOKENS=4096
uv run browser-to-mcp jobs worker
```

The endpoint must be literal `http://127.0.0.1:PORT`, without a path or query.
If needed, supply `BROWSER_TO_MCP_MODEL_API_KEY` privately in the environment.
The project does not download or install a model. The adapter formats the chat,
counts exact input token IDs, and reserves those tokens plus the output allowance
before inference. It sends the same token IDs to completion with the proposal
schema. Local inference makes no billable API request; hardware, electricity,
and hosting costs remain unmeasured. Server logs are managed separately.

## Task file

Save this example as `task.json`. It describes the public travel-book practice
page; it does not claim the site or a particular model has passed a live trial.
Review the URL and required hosts before running it.

```json
{
  "name": "travel_books",
  "start_url": "https://books.toscrape.com/catalogue/category/books/travel_2/index.html",
  "description": "Return every travel book's full title, displayed price and product link.",
  "allowed_domains": ["books.toscrape.com"],
  "inputs": {},
  "fields": {
    "title": {"description": "Full book title"},
    "price_text": {"description": "Displayed price with currency"},
    "product_url": {"description": "Product page link", "link_path_prefix": "/catalogue/"}
  },
  "shape": "array",
  "max_items": 30,
  "max_pages": 1,
  "timeout_seconds": 30,
  "allow_empty": false,
  "unique_by": "product_url"
}
```

Required: `name`, a concrete `start_url`, `description`, `allowed_domains`,
`inputs` (use `{}` if none), and 1–16 output `fields`. Each input/field needs a
description and can use `string` (default), `integer`, `number`, or `boolean`.
Keep displayed currency amounts as strings. Link fields require an allowed path
prefix, such as `/products/`. The model must preserve it.

For a search URL such as `?q=notebook`, an input can be:

```json
"inputs": {"query": {"description": "Product search", "example": "notebook"}}
```

Every input must bind to a whole query value or path segment. Optional examples
must match that starting value after decoding; the saved workflow then accepts
new values. There are at most 16 inputs. The model cannot change the fixed host
or other fixed URL parts.

Optional limits: `max_items` 1–100 (default 50), `max_pages` 1–20 (default 1), and
`timeout_seconds` 1–120 (default 30). `allow_empty` defaults false; true requires
an empty-state marker. `unique_by` removes identical duplicate rows but rejects
conflicts. `shape` is `object` or `array`; omitting it asks `single`/`list`.
Paging or allowing empty results already implies an array.

## Start and review

Generate a UUID with `python -c 'from uuid import uuid4; print(uuid4())'`, then use
it as `REQUEST_KEY` in another terminal:

```bash
uv run browser-to-mcp jobs start-task task.json --generation provider \
  --request-key REQUEST_KEY --browser-actions 4 --model-requests 1 \
  --tokens 32768 --cost-microusd 1000000
uv run browser-to-mcp jobs read JOB_ID
```

This example permits up to US$1 of configured model cost; choose your own budget.
Four actions cover browser startup, navigation, bounded inspection, and the
starting-page preview. Browser/model budgets default to zero until explicitly
allowed. Both modes keep the model request within the total job deadline.
When the draft is ready, use the review steps in the [main guide](TASK_BUILDER.md).

The MCP `start_task_build_job` tool also accepts `generation: "provider"` with the
same task and budgets. Model endpoints, keys and rates are configured only on the
worker. Existing provider jobs created before client mode was added keep their
provider behavior; new jobs default to client mode. Use a new request key to
change a job's generation mode.
