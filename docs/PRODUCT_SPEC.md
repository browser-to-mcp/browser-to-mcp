# Product Specification

## Status

This document describes the current V0 implementation and the boundaries for
the next milestones. Research notes and source comparisons live in the
[research repository](https://github.com/browser-to-mcp/research).

## Product

`browser-to-mcp` turns a reviewed browser workflow into a typed, permissioned,
replayable MCP tool.

```text
workflow YAML -> validate -> replay -> structured result + trace
```

The workflow is the source of truth. The runtime executes declared actions; it
does not ask an agent to rediscover the page on every run.

## V0 User

The first user is a developer who needs a reliable tool for a repeated,
read-only website task that has no suitable API.

The reference workflow searches Hacker News and returns visible page text:

```bash
uv run browser-to-mcp validate workflows/hn_search.yaml
uv run browser-to-mcp dry-run workflows/hn_search.yaml
uv run browser-to-mcp replay workflows/hn_search.yaml --input query=mcp
```

## Product Boundary

V0 does:

- load human-reviewable YAML workflow contracts;
- validate schema, permissions, step safety, domains, and assertions;
- replay declared read-only actions through Playwright;
- enforce a fresh browser context, request allowlist, action timeouts, and an
  overall run timeout;
- return structured outputs and action results;
- write redacted JSON traces and failure screenshots.
- inspect native WebMCP tool metadata without invoking native tools.

V0 does not:

- plan or repair workflows with an LLM;
- execute writes, payments, uploads, messages, or destructive actions;
- handle authentication-sensitive workflows;
- execute native WebMCP tools;
- provide hosted browsers, persistent profiles, or a public workflow registry.

## Workflow Contract

Each workflow declares its identity, permissions, typed inputs and outputs,
ordered steps, and success assertions.

Every step must explicitly declare:

```yaml
timeout_seconds: 30
risk: read_only
idempotency: safe
retry: none
confirmation: never
```

V0 supports `goto`, `fill`, `press`, `click`, `wait_for_text`,
`extract_text`, and permission-gated `download` actions. The workflow must be
`read_only`. Domains are explicit, and every navigation and page request is
checked against the allowlist.

## Runtime Flow

```text
YAML
  -> Pydantic model and policy validation
  -> typed input validation
  -> isolated PlaywrightReplayEngine
  -> declared actions and output extraction
  -> success assertions
  -> structured result or stable failure
  -> redacted trace
```

The replay engine is usable independently of the CLI so the future MCP server
can call the same interface.

## Safety Rules

The loader and replay boundary fail closed when:

- workflow or step risk is not `read_only`;
- a side-effect capability is enabled;
- authentication is required;
- a step omits a required safety declaration;
- retry and idempotency declarations conflict;
- confirmation is requested, because V0 has no approval UI;
- a download is undeclared or lacks download permission;
- navigation or a page request leaves the domain allowlist.

Failures use stable categories such as `schema`, `permission`, `policy`,
`input`, `assertion`, and `runtime`.

## Interfaces

Available now:

- `validate`: validate a workflow without opening a browser;
- `dry-run`: print the reviewed execution plan and safety declarations;
- `replay`: execute a workflow and emit structured JSON plus a trace;
- `server`: expose validated workflows over local STDIO MCP;
- `discover`: snapshot native WebMCP metadata and Permissions Policy state without
  invoking native tools.

Workflow-backed MCP tools and native WebMCP tools are separate interfaces. The
workflow server compiles reviewed YAML contracts and replays them through the
project's policy-bound engine. Native discovery only observes page-provided
metadata; it does not invoke, import, or convert those tools.

Historical implementation milestones:

- local STDIO MCP server and generated tool schemas ([issue #11](https://github.com/browser-to-mcp/browser-to-mcp/issues/11),
  [issue #13](https://github.com/browser-to-mcp/browser-to-mcp/issues/13));
- protocol-level MCP invocation tests ([issue #12](https://github.com/browser-to-mcp/browser-to-mcp/issues/12));
- native WebMCP discovery snapshots ([issue #14](https://github.com/browser-to-mcp/browser-to-mcp/issues/14)).

## Technical Choices

- Python 3.11+ and `uv`;
- Pydantic v2 for contracts and structured results;
- YAML for reviewed workflow files;
- Playwright Python for deterministic browser execution;
- Apache-2.0 license;
- official MCP Python SDK for the local workflow-backed server.

## V0 Completion Criteria

V0 is complete when a developer can validate a reviewed workflow, replay it
with typed input, expose it through a local MCP server, receive structured
output and provenance, and diagnose a deliberate failure from its trace.

The practical success metric is that a developer can turn a repeated,
read-only browser task into a reviewed tool in under ten minutes.
