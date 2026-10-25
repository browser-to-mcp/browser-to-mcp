"""Bounded record conversion, duplicate rules, and safe validation diagnostics."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, DecimalException

from browser_to_mcp.models import (
    MAX_CELL_CHARS,
    MAX_RECORD_BYTES,
    OutputValue,
    ScalarValue,
    StructuredOutput,
    Workflow,
)

EXPECTATIONS = {
    "string": "non-blank text",
    "integer": "a JSON integer",
    "number": "a finite JSON number representable without precision loss",
    "boolean": "an exact JSON boolean (true or false)",
}


@dataclass
class OutputValidationError(ValueError):
    path: str
    message: str

    def __str__(self):
        return f"{self.path}: {self.message}"


def scalar_value(raw: object, kind: str, path: str) -> ScalarValue:
    try:
        if not isinstance(raw, str):
            raise ValueError
        if kind == "string":
            if not raw.strip():
                raise ValueError
            return raw
        parsed = json.loads(raw, parse_float=Decimal, parse_constant=lambda _: None)
        if kind == "integer" and type(parsed) is int:
            return parsed
        if kind == "boolean" and type(parsed) is bool:
            return parsed
        if kind == "number" and type(parsed) in {int, Decimal}:
            value = float(parsed)
            if math.isfinite(value) and Decimal(str(value)) == parsed:
                return value
        raise ValueError
    except (ValueError, OverflowError, RecursionError, DecimalException):
        raise OutputValidationError(path, f"Output must contain {EXPECTATIONS[kind]}.") from None


def validate_outputs(workflow: Workflow, raw_outputs: dict[str, object]) -> dict[str, OutputValue]:
    for name in raw_outputs:
        if name not in workflow.outputs:
            raise OutputValidationError(
                f"outputs.{name}", "Output is not declared by the workflow."
            )
    outputs = {}
    for name, definition in workflow.outputs.items():
        path = f"outputs.{name}"
        if name not in raw_outputs:
            raise OutputValidationError(path, "Required output was not extracted.")
        raw = raw_outputs[name]
        if not isinstance(definition, StructuredOutput):
            outputs[name] = scalar_value(raw, definition.type, path)
            continue
        producer = next((s for s in workflow.steps if s.id == name), None)
        outputs[name] = validate_record_output(
            raw, definition, producer.record if producer else None, path
        )
    return outputs


def validate_record_output(raw, definition: StructuredOutput, extraction, path: str):
    """Validate one page or final output using the same conversion and identity rules."""
    if definition.type == "array":
        if not isinstance(raw, list) or len(raw) > definition.max_items:
            raise OutputValidationError(path, "Expected a list within the declared row limit.")
        if not raw and not definition.allow_empty:
            raise OutputValidationError(path, "An empty list is not allowed by this output.")
        records = raw
    else:
        if not isinstance(raw, dict):
            raise OutputValidationError(path, "Expected exactly one record.")
        records = [raw]
    converted = []
    for index, record in enumerate(records):
        row_path = f"{path}.{index}" if definition.type == "array" else path
        if not isinstance(record, dict) or set(record) != set(definition.record_fields):
            raise OutputValidationError(row_path, "Record fields must match the declared schema.")
        row = {}
        for field, schema in definition.record_fields.items():
            value = record[field]
            field_path = f"{row_path}.{field}"
            if value is None or isinstance(value, str) and not value.strip():
                if schema.nullable:
                    row[field] = None
                    continue
                raise OutputValidationError(
                    field_path, "A required record field is missing or blank."
                )
            if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
                raise OutputValidationError(field_path, "Record field exceeds the text limit.")
            row[field] = scalar_value(value, schema.type, field_path)
        converted.append(row)
    if extraction and extraction.duplicates != "keep":
        seen = {}
        unique = []
        for index, row in enumerate(converted):
            fingerprint = json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False)
            key = (
                json.dumps(row[extraction.unique_by], ensure_ascii=False, allow_nan=False)
                if extraction.unique_by
                else fingerprint
            )
            if key in seen:
                if extraction.duplicates == "reject" or seen[key] != fingerprint:
                    raise OutputValidationError(
                        f"{path}.{index}", "A duplicate or conflicting record was refused."
                    )
                continue
            seen[key] = fingerprint
            unique.append(row)
        converted = unique
    result = converted if definition.type == "array" else converted[0]
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > MAX_RECORD_BYTES:
        raise OutputValidationError(path, "Record result exceeds the JSON size limit.")
    return result
