"""Finite numbered-link collection on the existing policy-bound browser page."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from time import monotonic
from urllib.parse import parse_qsl, urlsplit

from browser_to_mcp.browser_policy import request_violation
from browser_to_mcp.models import MAX_RECORD_BYTES, MAX_RECORD_ROWS
from browser_to_mcp.records import cancellable_page_operation, extract_record_snapshot
from browser_to_mcp.structured_outputs import OutputValidationError, validate_record_output
from browser_to_mcp.tracing import TracePage


class PagingPermissionError(OutputValidationError):
    """A Next address refused by the shared policy before navigation."""


def _fingerprint(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _fail(step, message):
    raise OutputValidationError(f"steps.{step.id}.paging", message)


def _query(url):
    return parse_qsl(url.query, keep_blank_values=True, max_num_fields=100)


def check_page_url(initial_url, target_url, page_number, step, allowed_domains):
    """Require the original origin/filters and the exact next numbered page."""
    if len(target_url) > 4096:
        _fail(step, "The page address is too long.")
    violation = request_violation(target_url, "GET", allowed_domains, navigation=True)
    if violation:
        raise PagingPermissionError(f"steps.{step.id}.paging", violation.message)
    initial, target = urlsplit(initial_url), urlsplit(target_url)
    if (initial.scheme, initial.netloc) != (target.scheme, target.netloc) or target.fragment:
        _fail(step, "The next page changed its origin or added a fragment.")
    try:
        original_query, target_query = _query(initial), _query(target)
    except ValueError:
        _fail(step, "The page address has too many query fields.")
    paging = step.paging
    if paging.page_parameter:
        key = paging.page_parameter
        original_numbers = [v for k, v in original_query if k == key]
        expected = [] if page_number == 1 and not original_numbers else [str(page_number)]
        if original_numbers not in ([], ["1"]):
            _fail(step, "Collection must start at page one.")
        if [v for k, v in target_query if k == key] != expected or target.path != initial.path:
            _fail(step, "The next link did not advance to the expected page.")
        original_query = [(k, v) for k, v in original_query if k != key]
        target_query = [(k, v) for k, v in target_query if k != key]
    else:
        expected_path = (
            initial.path
            if page_number == 1
            else paging.page_path.replace("{page}", str(page_number))
        )
        if target.path != expected_path:
            _fail(step, "The next link changed the reviewed page path.")
    if Counter(original_query) != Counter(target_query):
        _fail(step, "The next page changed or dropped the original filters.")


def _raw_rows(rows):
    # The standard output processor still performs the final closed-schema validation.
    return [
        {
            key: value if value is None or isinstance(value, str) else json.dumps(value)
            for key, value in row.items()
        }
        for row in rows
    ]


async def collect_pages(page, step, output, allowed_domains, check_boundary, evidence, limit=None):
    """Return bounded raw outputs; failures/cancellation never return partial records."""
    initial_url = page.url
    rows = []
    seen = {}
    seen_pages = set()
    page_number = 1
    current_count = 0
    page_started = monotonic()
    per_page = output.model_copy(update={"max_items": MAX_RECORD_ROWS})

    def record(outcome, count):
        evidence.append(
            TracePage(
                page_number=page_number,
                rows_read=count,
                records_collected=len(rows),
                outcome=outcome,
                duration_seconds=max(0, monotonic() - page_started),
            )
        )

    try:
        async with asyncio.timeout(step.paging.timeout_seconds):
            check_page_url(initial_url, initial_url, 1, step, allowed_domains)
            while True:
                check_boundary()
                before = page.url
                raw, navigation = await extract_record_snapshot(page, step, per_page)
                current_count = len(raw)
                check_boundary()
                check_page_url(initial_url, page.url, page_number, step, allowed_domains)
                if page.url != before:
                    _fail(step, "The page changed during collection.")
                converted = validate_record_output(raw, per_page, step.record, f"outputs.{step.id}")
                # A repeated set is a loop even when its order or address changes.
                signature = tuple(sorted(_fingerprint(row) for row in converted))
                if signature in seen_pages:
                    _fail(step, "A previously read result page appeared again.")
                seen_pages.add(signature)
                if not converted and (page_number != 1 or not navigation["end"]):
                    _fail(step, "An empty page cannot prove this collection is complete.")
                additions = []
                for row in converted:
                    key = _fingerprint(row[step.record.unique_by])
                    fingerprint = _fingerprint(row)
                    if key in seen:
                        if seen[key] != fingerprint:
                            _fail(step, "The same record identity has conflicting values.")
                    else:
                        seen[key] = fingerprint
                        additions.append(row)
                room = output.max_items - len(rows)
                rows.extend(additions[:room])
                if len(_fingerprint(rows).encode()) > MAX_RECORD_BYTES:
                    _fail(step, "The combined result exceeded the JSON size limit.")
                # Check the link even when a budget means it will not be followed.
                if not navigation["end"]:
                    check_page_url(
                        initial_url, navigation["url"], page_number + 1, step, allowed_domains
                    )
                if len(additions) > room:
                    reason = "record_limit"
                elif navigation["end"]:
                    reason = "end"
                elif len(rows) == output.max_items:
                    reason = "record_limit"
                elif page_number == (step.paging.max_pages if limit is None else limit):
                    reason = "page_limit"
                else:
                    reason = "next"
                record(reason, len(raw))
                if reason != "next":
                    return _raw_rows(rows), {
                        "pages_read": str(page_number),
                        "complete": "true" if reason == "end" else "false",
                        "stop_reason": reason,
                    }
                page_number += 1
                current_count = 0
                page_started = monotonic()
                response = await cancellable_page_operation(
                    page,
                    page.goto(
                        navigation["url"],
                        wait_until="domcontentloaded",
                        timeout=step.timeout_seconds * 1000,
                    ),
                )
                check_boundary()
                if response is None or not 200 <= response.status < 300:
                    _fail(step, "The next page returned an unsuccessful response.")
    except BaseException as error:
        # Retain progress on timeout/cancellation too, without storing scraped values.
        record(
            "cancelled" if isinstance(error, asyncio.CancelledError) else "failed", current_count
        )
        raise
