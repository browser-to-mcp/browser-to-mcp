# Browser and network restrictions

Recording, authoring preview, CLI replay, and local MCP calls use the same
`BrowserBoundary` in `browser_policy.py`. Each session starts with a fresh
Chromium context. A refused request or capability makes a recording incomplete
or fails a run with `permission`. It blocks later requests in that context.
No hosts are added automatically.

A `read_only` label and an HTTP method cannot prove that a website has no side
effects. Even a GET link can change a cart or trigger a server action. Review
what the page actions do and use public pages with permission. These checks
reduce the supported browser surface; they do not make arbitrary websites safe.

## What the audit found

Before #58, selection preview inherited the recorder's browser setup, while
CLI and MCP replay created a different context. The differences were:

| Surface | Recording and selection before this change | Replay before this change |
| --- | --- | --- |
| Network methods | GET/HEAD/OPTIONS only | No method check |
| Redirects | Continued requests could follow an unchecked chain | Same gap |
| Frames and tabs | Diagnosed; only navigation requests checked their frame | No capability check |
| Workers and sockets | Service workers disabled; WebSockets closed | Default browser behavior |
| Permissions and dialogs | Some APIs guarded and dialogs dismissed | No shared capability guard |
| Downloads | Unsupported | Explicit steps supported, but unrelated downloads lacked a shared check |
| Sensitive URL diagnostics | Recorder used generic diagnostics | Blocked request errors could include the full URL |

There is one shared context factory now. The table below states the current
behavior. Future builder integration is specified at the end of this guide.

## Supported capabilities

| Capability | Recording and preview | Replay, including CLI/MCP |
| --- | --- | --- |
| HTTP(S) on declared hosts | Allowed after URL and method checks | Same |
| Subdomains | A declared host also permits its subdomains, as before | Same |
| Ports | The allowlist contains hosts, not ports; all ports on an allowed host match | Same |
| GET, HEAD, OPTIONS | Allowed; no automatic retries | Same |
| POST, PUT, PATCH, DELETE, other methods | Refused, including a POST that a site describes as read-only | Same |
| Credentials or common secret-bearing URL parameters | Refused without echoing the URL | Same |
| Ordinary public fragments | Allowed; secret-looking fragments refused | Same |
| HTTP redirects | Refused before following the first hop, including redirects to an allowed host | Same |
| New tabs and popups | Page opening is refused; an extra page is closed and its requests blocked | Same |
| Frames, including local `srcdoc` | Unsupported; attachment fails the session, child-frame network requests are blocked | Same |
| Service workers | Registration refused and browser context blocks service workers | Same |
| Dedicated and shared workers | Constructors refused; worker requests have no supported page frame | Same |
| WebSockets | Closed without connecting to the server | Same |
| WebTransport, EventSource, and WebRTC | Constructors refused by the shared page guard | Same |
| Camera, microphone, location, notifications, clipboard and device pickers | No browser permissions granted; guarded requests refused | Same |
| File chooser/upload | Refused; no file is supplied | Same |
| Downloads | Unsupported while recording or selecting outputs | Only a declared `download` step with `permissions.downloads: true`; returns filename only, file is cancelled and not kept |
| Unexpected attachment response | Refused before delivery to the page | Same, except during that declared download step |
| Browser dialogs | Dismissed; session fails | Same |
| `file:`, `data:`, `blob:` documents | Unsupported; only the initial empty `about:blank` page is permitted | Same |
| Saved accounts or browser profiles | Unsupported; no saved state loaded | Same |

The page guard runs before site scripts to refuse unsupported capability APIs.
HTTP and WebSocket routing is installed on the context, including auxiliary
pages. Frames may create local document content before their attachment is
reported, but their network requests cannot pass the original-page check.
These hooks are not an operating-system sandbox for malicious browser code.

## Why redirects are refused

Playwright's request routing does not recheck every redirect hop. The shared
handler fetches a single response with `max_redirects=0` and `max_retries=0`.
A redirect response with a Location header fails as `redirect`; it is never
released to the browser or followed by the fetch helper. This also catches
relative redirects and redirects of scripts or other subresources.

Allowed finite responses are passed to Chromium. This buffers response bodies;
streaming feeds, EventSource, and indefinitely open responses are outside this
release. Network fetching is bounded to 30 seconds and the existing step/run
timeouts still apply. Pages that require streaming or a read-only POST need a
separate reviewed design; there is no exception switch in a workflow.

## Existing workflows and migration

The bundled Hacker News and Wikipedia workflows keep their contract syntax.
Their validation and controlled browser regressions remain required checks;
live website reliability is a separate opt-in observation.

Replay previously checked domains alone. It now refuses methods and capabilities
that were already unsupported in recording, and all stages refuse redirects.
If an existing workflow fails:

1. For `redirect`, inspect the destination separately and review it. Change the
   authored URL to that final HTTP(S) page if it fits the task and permissions.
   Do not widen the domain list to follow an unknown chain.
2. For `write_request`, choose an approved GET page or supported API. Changing
   a risk label does not enable POST.
3. For `frame`, `worker`, `websocket`, `socket`, or `service_worker`, choose a
   page that works with this supported surface or defer the task.
4. For `file_transfer`, remove the action or explicitly review the existing
   filename-only download contract. Preview cannot author downloads.
5. For `unsafe_url`, remove credentials and secret parameters. Common secrets
   are detected heuristically; public paths and inputs still need review.

Malformed domain patterns are rejected consistently during validation and
recording. Use literal dotted hostnames (ASCII or punycode), without wildcards,
schemes, paths, or ports. `127.0.0.1` is accepted for explicitly allowed local
test pages. Matching still includes subdomains; an exact-host or path allowlist
is not provided by the current contract.

## Failures and private information

Boundary failures have a fixed code and explanation, such as `write_request`,
`blocked_request`, or `redirect`. Recording stores a diagnostic and marks its
draft partial. Preview refuses completion; replay reports `permission` with the
failed step and trace. Refusals take priority over a resulting browser timeout.
Network fetch failures use `runtime` with the generic `network_error` code;
they also omit URLs and screenshots. The first boundary failure is retained; later page events cannot clear it.

The shared boundary never retains request URLs, headers, bodies, dialog text,
page content, or downloaded bytes. Permission-failure screenshots are omitted,
even when inputs are not marked sensitive. Existing successful outputs and
explicit recording events still contain the public information selected by the
user. Other runtime errors retain the existing trace and screenshot rules;
mark sensitive inputs appropriately and keep diagnostic files private.

## Local use and future hosting

This release runs locally with explicit domains. It may contact private or
loopback addresses when their hosts are deliberately allowed. That is useful
for controlled tests and **is not a hosted network isolation policy**.
`create_context(environment="hosted")` refuses before creating a context.
There is no hosted worker or remote browser entry point in this release.

A future hosted worker must enforce its own outbound firewall, block internal
services, loopback, link-local and cloud metadata destinations, validate every
resolved address and redirect, and prevent DNS rebinding. It must not reuse the
local mode as a hosted exception. That work belongs to #67.

A future task builder (#62) must use this context factory and normal workflow
validation for every generated candidate and test. No builder exists yet.
`execute_steps` alone is a low-level action helper, not a protected browser
factory. The separate native WebMCP `discover` command is an experimental page
inspection path, not a workflow entry point or a substitute for this boundary.
It has not gained these workflow guarantees.

## Controlled checks

`tests/test_browser_policy.py` serves original local hostile pages and exercises
recording, authoring preview, and production replay. It checks the same refusal
codes, server request counters, redacted traces, and absence of screenshots for
permission failures. Cases cover denied methods and domains, secret URLs,
redirects, frames, tabs, workers, sockets, permissions, dialogs, and downloads.
Positive cases verify GET/HEAD/OPTIONS and explicitly allowed loopback. The
existing browser-to-MCP journey continues to test actual STDIO calls and cleanup.
CI never needs a public catalog or a third-party account.

The implementation follows Playwright's documented
[one-response fetch options](https://playwright.dev/python/docs/api/class-route#route-fetch)
and [context routing and service-worker restrictions](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route).
