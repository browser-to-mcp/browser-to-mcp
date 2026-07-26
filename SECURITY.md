# Security policy

## Supported scope

The supported release scope is the local, read-only workflow runtime. It is
designed to refuse writes, destructive actions, payments, uploads, messaging,
and authentication-sensitive workflows before a browser starts.

## Reporting a vulnerability

Please do not publish sensitive details in a public issue. Use
[GitHub Security Advisories](https://github.com/browser-to-mcp/browser-to-mcp/security/advisories/new)
when available. Include the affected commit or release, reproduction steps,
impact, and any suggested mitigation.

If private advisories are unavailable, open a minimal public issue asking for
a private reporting channel without including exploit details.

## Handling reports

Reports are triaged before public discussion. A fix should include a focused
regression test where practical and document any change to the supported V0
security boundary.
