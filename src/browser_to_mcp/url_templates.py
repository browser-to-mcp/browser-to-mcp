"""Shared URL template grammar for validation and replay."""

from __future__ import annotations

import re
from urllib.parse import SplitResult, urlsplit

TEMPLATE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_URL_PATTERN = re.compile(
    r"(?P<scheme>[^:/?#]+)://(?P<authority>[^/?#]+)"
    r"(?P<path>[^?#]*)(?:\?(?P<query>[^#]*))?(?:#(?P<fragment>.*))?"
)


def validate_url_template(template: str) -> SplitResult:
    """Allow inputs only in query values and whole, individual path segments.

    Inspect the authored components before substituting anything. Parsing alone
    cannot reliably identify placeholders in schemes or malformed authorities.
    Never decode or rebuild fixed URL text: its escaping belongs to the author.
    """

    masked = TEMPLATE_PATTERN.sub("input", template)
    if "{{" in masked or "}}" in masked:
        raise ValueError("URL placeholders must use {{input_name}} without expressions.")
    for placeholder in TEMPLATE_PATTERN.finditer(template):
        if re.search(r"%[0-9a-fA-F]?$", template[: placeholder.start()]):
            raise ValueError("A URL placeholder cannot complete a fixed percent escape.")
    if "\\" in masked or any(ord(char) <= 32 or ord(char) == 127 for char in masked):
        raise ValueError("Fixed URL text cannot contain whitespace, controls, or backslashes.")
    match = _URL_PATTERN.fullmatch(template)
    if match is None:
        raise ValueError("A goto step requires an absolute HTTP(S) URL with a hostname.")

    for component in ("scheme", "authority", "fragment"):
        if TEMPLATE_PATTERN.search(match[component] or ""):
            raise ValueError(
                "URL placeholders are supported only in query values or whole path segments; "
                "scheme, hostname, port, credentials, and fragment must be fixed."
            )
    for segment in match["path"].split("/"):
        if TEMPLATE_PATTERN.search(segment) and not TEMPLATE_PATTERN.fullmatch(segment):
            raise ValueError("A path placeholder must occupy one entire path segment.")
    for parameter in (match["query"] or "").split("&"):
        name, _, _value = parameter.partition("=")
        if TEMPLATE_PATTERN.search(name):
            raise ValueError("URL placeholders cannot appear in query parameter names.")

    try:
        parsed = urlsplit(template)
        host = parsed.hostname
        # Accessing port also rejects invalid literal ports before browser startup.
        parsed.port
    except ValueError as error:
        raise ValueError("A goto step requires a valid fixed hostname and port.") from error
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("A goto step requires an absolute HTTP(S) URL with a hostname.")
    return parsed
