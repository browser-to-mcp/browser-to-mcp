# Contributing

## Setup

The project requires Python 3.11 or newer, `uv`, and a Playwright Chromium
installation:

```bash
uv sync --all-groups
uv run playwright install chromium
```

## Checks

Before opening a pull request, run:

```bash
uv run pytest
uv run ruff check .
uv run browser-to-mcp validate workflows/hn_search.yaml
uv build
```

Behavior changes should include focused tests. Keep workflow changes
reviewable and preserve the explicit timeout, risk, idempotency, retry, and
confirmation declarations on every step.

## Scope

The current V0 runtime is read-only. Do not add writes, payments, uploads,
messaging, authentication-sensitive behavior, or destructive browser actions
without a separate design and security review.

## Pull requests

Explain the behavior change, safety impact, validation commands, and any live
website dependency. Keep commits small enough that each one maps to a single
implementation concern.
