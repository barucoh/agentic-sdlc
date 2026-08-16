"""Reference model for Agentic SDLC cross-task delivery semantics.

This module validates scenarios and documents executable state transitions. It does
not persist transport state or perform external side effects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "bootstrap-agentic-sdlc"
    / "assets"
    / "repository"
    / ".agentic-sdlc"
    / "handoff.schema.json"
)
SUPPORTED_SCHEMA_KEYWORDS = {
    "$schema",
    "$id",
    "$defs",
    "$ref",
    "title",
    "type",
    "additionalProperties",
    "required",
    "properties",
    "const",
    "enum",
    "pattern",
    "format",
    "minLength",
    "minimum",
    "maximum",
    "items",
    "uniqueItems",
}


class DeliveryState(str, Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    NOT_DELIVERED = "NOT_DELIVERED"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"


class Observation(str, Enum):
    DELIVERED_AND_ACKNOWLEDGED = "delivered_and_acknowledged"
    EXPLICIT_NON_DELIVERY = "explicit_non_delivery"
    DELIVERED_BUT_ACK_FAILED = "delivered_but_ack_failed"
    TIMEOUT = "timeout"
    HANDLER_FAILURE = "handler_failure"


class Reconciliation(str, Enum):
    APPLIED = "applied"
    ABSENT = "absent"
    UNAVAILABLE = "unavailable"


@dataclass
class TargetOperation:
    operation_id: str
    target: str
    authoritative_ref: str
    objective: str
    expected_output: str = "Structured handoff with authoritative evidence"
    evidence: str = "Authoritative GitHub issue, PR, commit, review, or check"
    next_owner: str = "coordinator"
    state: DeliveryState = DeliveryState.PENDING
    attempt_count: int = 0
    retry_allowed: bool = False
    side_effect_confirmed: bool = False
    reconciliation_count: int = 0
    last_reconciliation: Reconciliation | None = None

    def observe(self, observation: Observation, authoritative_confirmed: bool = False) -> DeliveryState:
        if self.state is DeliveryState.DELIVERED or self.side_effect_confirmed:
            return DeliveryState.DELIVERED
        self.attempt_count += 1
        self.retry_allowed = False
        if observation is Observation.EXPLICIT_NON_DELIVERY:
            if self.state is DeliveryState.DELIVERY_UNKNOWN:
                self.state = DeliveryState.DELIVERY_UNKNOWN
            else:
                self.state = DeliveryState.NOT_DELIVERED
                self.retry_allowed = True
        elif observation is Observation.DELIVERED_AND_ACKNOWLEDGED and authoritative_confirmed:
            self.state = DeliveryState.DELIVERED
            self.side_effect_confirmed = True
        else:
            self.state = DeliveryState.DELIVERY_UNKNOWN
        return self.state

    def reconcile(self, result: Reconciliation) -> DeliveryState:
        if self.state is DeliveryState.DELIVERED or self.side_effect_confirmed:
            return DeliveryState.DELIVERED
        self.reconciliation_count += 1
        self.last_reconciliation = result
        self.retry_allowed = False
        if result is Reconciliation.APPLIED:
            self.state = DeliveryState.DELIVERED
            self.side_effect_confirmed = True
        elif result is Reconciliation.ABSENT:
            self.state = DeliveryState.NOT_DELIVERED
            self.retry_allowed = True
        else:
            self.state = DeliveryState.DELIVERY_UNKNOWN
        return self.state

    def fallback(self) -> dict[str, str]:
        return {
            "operation_id": self.operation_id,
            "target": self.target,
            "authoritative_ref": self.authoritative_ref,
            "objective": self.objective,
            "expected_output": self.expected_output,
            "evidence": self.evidence,
            "next_owner": self.next_owner,
            "instruction": "Reconcile this operation against authoritative GitHub state before applying or retrying side effects.",
        }


class OperationLedger:
    """In-memory scenario ledger; committed thread IDs and transport databases are prohibited."""

    def __init__(self) -> None:
        self._operations: dict[str, TargetOperation] = {}

    def register(self, operation: TargetOperation) -> TargetOperation:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        operation_pattern = schema["properties"]["operation_id"]["pattern"]
        if re.fullmatch(operation_pattern, operation.operation_id) is None:
            raise ValueError("operation ID does not conform to the handoff schema")
        existing = self._operations.get(operation.operation_id)
        if existing:
            identity = ("target", "authoritative_ref", "objective", "expected_output", "next_owner")
            if any(getattr(existing, field) != getattr(operation, field) for field in identity):
                raise ValueError("operation ID reuse across actions is prohibited")
            return existing
        self._operations[operation.operation_id] = operation
        return operation

    def get(self, operation_id: str) -> TargetOperation:
        return self._operations[operation_id]


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported schema reference: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    if not isinstance(node, dict):
        raise ValueError(f"schema reference is not an object: {ref}")
    return node


def _schema_support_errors(schema: dict[str, Any], path: str = "schema") -> list[str]:
    errors = [f"{path} uses unsupported keyword {key}" for key in schema if key not in SUPPORTED_SCHEMA_KEYWORDS]
    if "format" in schema and schema["format"] != "uri":
        errors.append(f"{path} uses unsupported format {schema['format']!r}")
    if "additionalProperties" in schema and schema["additionalProperties"] is not False:
        errors.append(f"{path} uses unsupported additionalProperties value")
    for collection in ("properties", "$defs"):
        for name, child in schema.get(collection, {}).items():
            if isinstance(child, dict):
                errors.extend(_schema_support_errors(child, f"{path}.{collection}.{name}"))
    if isinstance(schema.get("items"), dict):
        errors.extend(_schema_support_errors(schema["items"], f"{path}.items"))
    return errors


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _valid_uri(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and (parsed.netloc or parsed.scheme not in {"http", "https"}))


def _validate_schema(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
) -> list[str]:
    if "$ref" in schema:
        return _validate_schema(value, _resolve_ref(root, schema["$ref"]), root, path)

    errors: list[str] = []
    expected_types = schema.get("type")
    if expected_types is not None:
        choices = [expected_types] if isinstance(expected_types, str) else expected_types
        if not any(_matches_type(value, expected) for expected in choices):
            return [f"{path} must have type {' or '.join(choices)}"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']!r}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}.{required} is required")
        if schema.get("additionalProperties") is False:
            for key in sorted(value.keys() - properties.keys()):
                errors.append(f"{path}.{key} is not allowed")
        for key, child in properties.items():
            if key in value:
                errors.extend(_validate_schema(value[key], child, root, f"{path}.{key}"))

    if isinstance(value, list):
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path} must contain unique items")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(_validate_schema(item, schema["items"], root, f"{path}[{index}]"))

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path} is shorter than {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path} does not match {schema['pattern']}")
        if schema.get("format") == "uri" and not _valid_uri(value):
            errors.append(f"{path} must be an absolute URI")

    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} must be at most {schema['maximum']}")
    return errors


def validate_handoff(value: Any, schema: dict[str, Any] | None = None) -> list[str]:
    authoritative_schema = schema or json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    compatibility_errors = _schema_support_errors(authoritative_schema)
    return compatibility_errors or _validate_schema(value, authoritative_schema, authoritative_schema, "handoff")
