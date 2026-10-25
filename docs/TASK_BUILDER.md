# Make a draft from a website and a task

Describe a public listing or URL-based search. A local model chooses the page
selectors: rules that find the text and links you want. You do not write them.
The result is a **draft for review**. Keeping it does not publish a callable tool.

Supports one record, a bounded list, and [numbered Next-link paging](PAGING.md).
The [recorder](RECORDING.md) and [authoring flow](AUTHORING.md) remain available.

## 1. Start the local model and worker

Install the project and browser:

```bash
uv sync --locked --all-groups
uv run playwright install chromium
```

Use a [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
with an instruction-following model you already have permission to use.
Replace the file path below. This project does not download a model.

```bash
llama-server -m /absolute/path/to/model.gguf \
  --host 127.0.0.1 --port 8080 --ctx-size 32768 --parallel 1 --log-disable
```

In another terminal, start the worker and leave it open:

```bash
export BROWSER_TO_MCP_MODEL_URL=http://127.0.0.1:8080
export BROWSER_TO_MCP_MODEL_MAX_TOKENS=4096
uv run browser-to-mcp jobs worker
```

The worker supports macOS and Linux. The model address must be
`http://127.0.0.1:PORT`, without a path or query. Cloud models are not supported.
If needed, set `BROWSER_TO_MCP_MODEL_API_KEY` through your normal secret manager
in the worker's environment. Never put a key in a task or answer. It is sent
only in the local server's Authorization header, outside the model prompt.
Memory needs, speed, and answer quality depend on your model.

## 2. Describe the task

Save this example as `task.json`:

```json
{
  "name": "travel_books",
  "start_url": "https://books.toscrape.com/catalogue/category/books/travel_2/index.html",
  "description": "Read every travel book in this listing. Return its full title, displayed price, and product page link. Do not open shopping controls.",
  "allowed_domains": ["books.toscrape.com"],
  "inputs": {},
  "fields": {
    "title": {"description": "Full book title, not shortened display text"},
    "price_text": {"description": "Displayed price with its currency mark"},
    "product_url": {
      "description": "Book product page link",
      "link_path_prefix": "/catalogue/"
    }
  },
  "shape": "array",
  "max_items": 30,
  "max_pages": 1,
  "timeout_seconds": 30,
  "allow_empty": false,
  "unique_by": "product_url"
}
```

This shows the request format; it is **not a verified live-model result**.
Check that your task is permitted. Public pages and supporting hosts can change.
The [pilot plan](THREE_SITE_PILOT.md) has the proposed sites and dated observations.

| Setting | Meaning |
| --- | --- |
| `name`, `start_url`, `description`, `allowed_domains` | Required tool name, concrete page URL, task, and permitted hosts |
| `inputs` | Required; up to 16. Use `{}` if nothing changes between calls. |
| `fields` | Required output names and descriptions; 1–16 fields |
| Field `type` | `string` by default; also `integer`, `number`, or `boolean`. Keep displayed prices as strings. |
| `link_path_prefix` | Required for a link field; the model cannot broaden it |
| `shape` | `object` for one record; `array` for a list. If omitted, the job asks `single` or `list`. Paging or empty results already imply a list. |
| `max_items` | List limit, 1–100; default 50 |
| `max_pages` | Page limit, 1–20; default 1 |
| `timeout_seconds` | Extraction/paging limit, 1–120; default 30 |
| `allow_empty` | Default false. True requires an explicit empty-state marker. |
| `unique_by` | Optional declared field used to remove identical duplicates; conflicting rows fail |

For a reusable search, put an actual search value in the URL, such as
`?q=notebook`, and replace `inputs: {}` with:

```json
"inputs": {
  "query": {"type": "string", "description": "Product search text", "example": "notebook"}
}
```

The model binds each input to a whole query value or path segment. An example
must match that URL position after decoding; examples are optional. The saved
URL becomes, for example, `?q={{query}}`. The host and other fixed URL parts stay
fixed. Every declared input is required when calling the resulting workflow.
Missing required task details are rejected before a job is created.

## 3. Start, follow, and review

In another terminal, generate a UUID and use it for `REQUEST_KEY` below:

```bash
python -c 'from uuid import uuid4; print(uuid4())'
uv run browser-to-mcp jobs start-task task.json --request-key REQUEST_KEY \
  --browser-actions 4 --model-requests 1 --tokens 32768
uv run browser-to-mcp jobs read JOB_ID
```

Use the printed job ID. Stages are `inspect_page`, `ask_model`, and `check_draft`.
Reading status never repeats work. The same key, task, and budgets return the
same job; use a new key for a deliberate new attempt.

For any question, use its **current** question ID and revision:

```bash
uv run browser-to-mcp jobs answer JOB_ID \
  --question-id QUESTION_ID --revision REVISION --value list
```

Choose `single` or `list` for a shape question. At `ready_for_review`, read the
`.draft.candidate` file named by `artifact.filename` inside `.browser-to-mcp-jobs/`.
Check its inputs, hosts, selectors, link prefixes, limits, and paging rules.
Answer the review question with `keep` or `discard`, using the same command.
Keeping finishes the job. Independent testing and publication are later steps.
A first-page preview does not prove later pages or different inputs are correct.

Cancel with `uv run browser-to-mcp jobs cancel JOB_ID`. For running work, wait
until status says `cancelled`. The worker closes the browser and model connection
and stops the phase. The external model server may take longer to stop computing.
Failed or cancelled builds never register a tool.

## Limits, privacy, and saved results

Four browser actions cover startup, opening the starting page, reading its page
view, and checking the proposed extraction with the normal record extractor.
The build does not open later pages. Browser/model budgets default to zero;
the command above explicitly permits four actions and one inference request.

The adapter formats and tokenizes the prompt without inference, then reserves
its exact input-token count plus the allowed output tokens before completion.
These setup calls still contact your local model server and use total job time.
If the reservation exceeds the token budget, inference does not start. Choose
a smaller output allowance or explicitly raise the budget; the server also needs
enough context space. Cut-off or inconsistent responses fail. No retries, token
refunds, redirects, or environment proxy settings are used.
Tokens measure how much text the model reads and writes.

The default total time is 900 seconds, including queue time and human answers.
The default output budget is 262,144 bytes across the page view, model answer,
first-page result, and draft. Failed attempts can still consume model time.
See [job limits, crashes, private files, and retention](BUILD_JOBS.md) for all
budget ranges, recovery rules, and the seven-day terminal-record cleanup.

The saved `report` lists the adapter, actual reported input/output tokens,
completion-request duration, browser lifetime through preview, and passed checks:
schema, policy, compiler, and first-page preview. Job usage counts are conservative
reservations. Local inference makes no billable API request, so API charges are
zero; hardware, electricity, and hosting costs are unmeasured. Model duration
excludes template formatting and token counting.

The [normal browser policy](BROWSER_POLICY.md) applies in a fresh context.
Declared hosts also allow their subdomains, not just one path. Unexpected required
hosts cause failure; review their purpose before changing permissions.
Login, forms, shopping, messages, downloads, challenges, infinite scrolling,
and JavaScript-only Next buttons are unsupported.

The page view waits 500 ms, then reads visible text, selected attributes, and
element paths. Limits are 600 elements, depth 18, and 65,536 bytes; larger views
fail. Scripts, input values, editable areas, and frames are excluded. Password
fields cause refusal. Slower or more complex pages may need another starting page.
Known credential-like text and unsafe links are omitted, but this cannot detect
every private value. Use public pages and public examples only.

Page instructions cannot grant permissions or publish tools. The model supplies
only selectors, input bindings, and supported paging settings. Trusted code builds
and validates the workflow; generated code and extra capabilities are rejected.
Wrong selectors can still select wrong public text, so review and independent
expected answers matter. Prompts, page views, and raw model replies are not saved.
Task snapshots and drafts are private files; model-server logs are managed separately.

## MCP and testing status

The standalone job MCP server adds `start_task_build_job(request_key, task, budgets)`.
Follow it with `read_build_job`, `answer_build_job`, and `cancel_build_job`.
Use the same absolute private directory for clients and worker; configure the
model only on the worker. See [MCP setup](BUILD_JOBS.md#local-mcp-access).

CI uses three original local layouts and controlled model replies. It checks
changed inputs, empty results, paging, replay traces, questions, refusals, budgets,
malformed proposals, hostile page instructions, cancellation, and the real
CLI/worker/HTTP/MCP path. These checks do not measure real-model accuracy or demand.

Live model builds on the three public pilot sites remain unmeasured. A live report
must include date, model/server versions, task/draft hashes, every started attempt
and outcome, actual tokens, reserved limits, elapsed time, charges, and independent
result checks. Include failures and cancellations. The wider pilot targets remain
pending; do not count controlled replies as live model success.
