# PyPI search support decision

Decision on 2026-10-20: **PyPI browser search is unsupported in this project.**
Do not add a `pypi_search` reference workflow or imply it is reliable.

Issue [#36](https://github.com/browser-to-mcp/browser-to-mcp/issues/36) records a
2026-07-28 Chromium attempt to search for `fastapi`. Navigation completed, but
the page showed a browser challenge instead of package results. Extraction then
timed out after thirty seconds with category `runtime` at `steps.results`.
This is historical evidence, not a claim that a fresh browser check was run today.

## Supported upstream alternatives

The official [PyPI API guide](https://docs.pypi.org/api/) directs new integrations
toward JSON, RSS, and Index APIs rather than XML-RPC. The
[JSON API](https://docs.pypi.org/api/json/) provides project metadata at
`/pypi/<project_name>/json`. That can support lookup when the package name is
already known. It is not free-text package search and is not browser replay.
The official [pip search documentation](https://pip.pypa.io/en/stable/cli/pip_search/)
says PyPI no longer supports the XML-RPC search used by that command.
These documents were checked on 2026-10-20.

A separate API-backed exact-name lookup could be designed later. This change
adds no API tool and no implicit fallback. It does not replace a failed browser
result with unrelated API data or introduce any challenge-solving behavior.

## Reproducibility and reconsideration

The decision needs no repeated traffic to a challenged search page. To re-check
support later:

1. Read the official API and search documentation above. Record the date and any
   changed supported interface. Distinguish search from exact-name lookup.
2. If a browser check is justified, use a fresh ordinary browser and the existing
   read-only policy with explicit domains. Open the public search page once.
3. Stop if a challenge appears. Do not solve it, retry aggressively, copy browser
   state, add stealth settings, or weaken permissions. Do not save or publish the
   challenge HTML or screenshots.
4. Record only the date, query, attempt count, whether actual results appeared,
   duration, and a redacted failure category/path. A challenge is not a result.
5. Reconsider browser support only after repeated ordinary read-only runs return
   meaningful package results without bypasses. Until then, keep it unsupported.

Normal runtime failures remain diagnosable using the existing trace schema.
No replay, timeout, allowlist, or error-classification behavior was changed for
this investigation. Live PyPI checks are deliberately outside deterministic CI.
