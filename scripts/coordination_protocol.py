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
SESSION_TITLE_FORMAT = "#{issue_number} {role_code} - {issue_title}"
SESSION_TITLE_MAX_CHARACTERS = 36
ROLE_CODES = {
    "coordinator": "CO",
    "product": "PD",
    "architecture": "AR",
    "implementation": "IM",
    "qa": "QA",
    "reviewer": "RV",
    "knowledge_steward": "KS",
}
TARGET_MODELS = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
EFFORT_LEVELS = ("Low", "Medium", "High")
ROUTING_MATRIX = {
    "coordinator": {"gpt-5.6-sol": {"Medium"}},
    "product": {"gpt-5.6-sol": {"Medium"}},
    "architecture": {"gpt-5.6-sol": {"Medium"}},
    "implementation": {
        "gpt-5.6-luna": {"Low"},
        "gpt-5.6-terra": {"Low", "Medium", "High"},
    },
    "qa": {"gpt-5.6-sol": {"Medium", "High"}},
    "reviewer": {"gpt-5.6-sol": {"Medium", "High"}},
    "knowledge_steward": {
        "gpt-5.6-luna": {"Low"},
        "gpt-5.6-sol": {"Medium"},
    },
    "human_owner": {"gpt-5.6-sol": {"Medium"}},
}
ROUTING_POLICY_VERSION = "repository-native-v1"
ROUTING_CONFIG_LINES = (
    "routing_policy: repository-native-v1",
    "routing_matrix:",
    '  coordinator: "gpt-5.6-sol/Medium"',
    '  product: "gpt-5.6-sol/Medium"',
    '  architecture: "gpt-5.6-sol/Medium"',
    '  implementation: "gpt-5.6-luna/Low|gpt-5.6-terra/Low..High"',
    '  qa: "gpt-5.6-sol/Medium|High-with-risk-rationale"',
    '  reviewer: "gpt-5.6-sol/Medium|High-with-risk-rationale"',
    '  knowledge_steward: "gpt-5.6-luna/Low|gpt-5.6-sol/Medium-with-decision-rationale"',
    '  ephemeral_research: "gpt-5.6-luna/Low|gpt-5.6-terra/Low|gpt-5.6-sol/exceptional-rationale"',
)
LIFECYCLE_CONFIG_LINES = (
    "lifecycle_policy: coordinator-owned-v1",
    'lifecycle_sequence: "IMPLEMENTATION_ACTIVE->IMPLEMENTATION_READY->REVIEW_ACTIVE->CHANGES_REQUESTED->CORRECTION_ACTIVE->IMPLEMENTATION_READY->REVIEW_ACTIVE->REVIEW_ACCEPTED->HUMAN_MERGE_READY"',
    "lifecycle_transport_states: BLOCKED, DELIVERY_UNKNOWN",
)


def format_session_title(issue_number: int, role_code: str, issue_title: str) -> str:
    """Build the canonical prospective session title using Unicode code points."""

    if isinstance(issue_number, bool) or not isinstance(issue_number, int) or issue_number < 1:
        raise ValueError("issue number must be a positive integer")
    if role_code not in ROLE_CODES.values():
        raise ValueError(f"unknown role code: {role_code!r}")
    if not isinstance(issue_title, str) or not issue_title:
        raise ValueError("issue title must be a non-empty string")

    prefix = f"#{issue_number} {role_code} - "
    complete = prefix + issue_title
    if len(complete) <= SESSION_TITLE_MAX_CHARACTERS:
        return complete

    title_budget = SESSION_TITLE_MAX_CHARACTERS - len(prefix)
    if title_budget < 1:
        raise ValueError("issue number leaves no room for the issue-title segment")
    visible_title = issue_title[: title_budget - 1].rstrip("…")
    return prefix + visible_title + "…"


def session_title_for_role(issue_number: int, role: str, issue_title: str) -> str:
    try:
        role_code = ROLE_CODES[role]
    except KeyError as exc:
        raise ValueError(f"unknown role: {role!r}") from exc
    return format_session_title(issue_number, role_code, issue_title)


def validate_session_title_config(config_text: str) -> list[str]:
    """Validate the managed YAML subset without adding a YAML dependency."""

    errors: list[str] = []
    expected_format = f"session_title_format: {json.dumps(SESSION_TITLE_FORMAT)}"
    if expected_format not in config_text.splitlines():
        errors.append(f"session_title_format must be {SESSION_TITLE_FORMAT!r}")
    expected_maximum = f"session_title_max_characters: {SESSION_TITLE_MAX_CHARACTERS}"
    if expected_maximum not in config_text.splitlines():
        errors.append(f"session_title_max_characters must be {SESSION_TITLE_MAX_CHARACTERS}")

    match = re.search(r"(?m)^role_codes:\n((?:  [a-z_]+: [A-Z]{2}\n)+)", config_text)
    if match is None:
        errors.append("role_codes must be a mapping of role names to two-character codes")
    else:
        configured = {}
        for line in match.group(1).splitlines():
            role, code = line.strip().split(": ", 1)
            configured[role] = code
        if configured != ROLE_CODES:
            errors.append(f"role_codes must equal {ROLE_CODES!r}")
    lines = config_text.splitlines()
    missing_routing_lines = [line for line in ROUTING_CONFIG_LINES if line not in lines]
    if missing_routing_lines:
        errors.append("routing policy is incomplete: " + ", ".join(missing_routing_lines))
    missing_lifecycle_lines = [line for line in LIFECYCLE_CONFIG_LINES if line not in lines]
    if missing_lifecycle_lines:
        errors.append("lifecycle policy is incomplete: " + ", ".join(missing_lifecycle_lines))
    return errors


def validate_routing(value: dict[str, Any]) -> list[str]:
    """Enforce the repository-native model/effort policy and explicit activation."""

    errors: list[str] = []
    target_role = value.get("to_role")
    model = value.get("target_model")
    effort = value.get("effort")
    mode = value.get("execution_mode")
    sandbox = value.get("sandbox_mode")
    rationale = value.get("rationale", "")
    if mode == "ephemeral_research":
        if model not in TARGET_MODELS:
            errors.append(f"ephemeral_research cannot route to target model {model!r}")
        elif model == "gpt-5.6-luna" and effort != "Low":
            errors.append("ephemeral Luna routing is limited to Low effort")
        elif model == "gpt-5.6-terra" and effort != "Low":
            errors.append("ephemeral Terra routing is limited to Low effort")
        elif model == "gpt-5.6-sol" and "exceptional" not in rationale.lower():
            errors.append("ephemeral Sol routing requires an explicit exceptional rationale")
        if sandbox != "read-only":
            errors.append("ephemeral_research handoffs must be read-only")
    elif mode == "durable":
        if target_role not in ROUTING_MATRIX:
            return [f"handoff.to_role has no routing policy: {target_role!r}"]
        allowed_models = ROUTING_MATRIX[target_role]
        if model not in TARGET_MODELS or model not in allowed_models:
            errors.append(f"{target_role} cannot route to target model {model!r}")
        elif effort not in allowed_models[model]:
            errors.append(f"{target_role} cannot use effort {effort!r} with {model}")
        expected_sandbox = "workspace-write" if target_role in {"implementation", "knowledge_steward"} else "read-only"
        if sandbox != expected_sandbox:
            errors.append(f"durable {target_role} handoff must use sandbox_mode={expected_sandbox!r}")
    else:
        errors.append(f"unknown execution mode: {mode!r}")

    if mode == "durable" and model == "gpt-5.6-terra" and effort in {"Medium", "High"}:
        lowered = rationale.lower()
        if "risk" not in lowered and "complex" not in lowered:
            errors.append("Terra effort above Low requires an explicit risk/complexity rationale")
    if mode == "durable" and target_role in {"qa", "reviewer"} and effort == "High":
        if "risk" not in rationale.lower():
            errors.append(f"{target_role} High effort requires an explicit high-risk rationale")
    if mode == "durable" and target_role == "knowledge_steward" and model == "gpt-5.6-sol" and "decision" not in rationale.lower():
        errors.append("Knowledge Steward Sol routing requires a decision-heavy rationale")

    if value.get("is_correction"):
        if target_role != "implementation":
            errors.append("corrections must return to the implementation role")
        if model not in {"gpt-5.6-luna", "gpt-5.6-terra"}:
            errors.append("corrections must use an allowed implementation model")
        if mode != "durable":
            errors.append("corrections require durable delivery")
    if mode == "durable" and target_role == "reviewer" and model != "gpt-5.6-sol":
        errors.append("Reviewer activation must use gpt-5.6-sol")
    return errors


def validate_lifecycle_handoff(value: dict[str, Any]) -> list[str]:
    state = value.get("lifecycle_state")
    work_item = value.get("work_item", {})
    evidence = [str(item).lower() for item in value.get("evidence", [])]
    errors: list[str] = []
    if state == LifecycleState.IMPLEMENTATION_READY.value:
        if not re.fullmatch(r"[0-9a-f]{40}", str(work_item.get("commit_sha") or "")):
            errors.append("IMPLEMENTATION_READY requires an exact commit SHA")
        if not work_item.get("pull_request_url"):
            errors.append("IMPLEMENTATION_READY requires a pull request URL")
        if not any("local gate" in item for item in evidence):
            errors.append("IMPLEMENTATION_READY requires local-gate evidence")
        if not any("ci" in item for item in evidence):
            errors.append("IMPLEMENTATION_READY requires CI evidence")
    if state == LifecycleState.REVIEW_ACTIVE.value:
        if value.get("to_role") != "reviewer":
            errors.append("REVIEW_ACTIVE handoffs must explicitly activate Reviewer")
        if value.get("target_model") != "gpt-5.6-sol" or value.get("effort") != "Medium":
            errors.append("REVIEW_ACTIVE must activate Reviewer with Sol/Medium")
        if not work_item.get("commit_sha") or not work_item.get("pull_request_url"):
            errors.append("REVIEW_ACTIVE requires the exact implementation SHA and PR URL")
    if state == LifecycleState.CORRECTION_ACTIVE.value:
        if value.get("to_role") != "implementation" or not value.get("is_correction"):
            errors.append("CORRECTION_ACTIVE must return to the same Implementation task")
    if state == LifecycleState.HUMAN_MERGE_READY.value and value.get("terminal_state") != "completed":
        errors.append("HUMAN_MERGE_READY requires completed non-human gates")
    return errors


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


class LifecycleState(str, Enum):
    IMPLEMENTATION_ACTIVE = "IMPLEMENTATION_ACTIVE"
    IMPLEMENTATION_READY = "IMPLEMENTATION_READY"
    REVIEW_ACTIVE = "REVIEW_ACTIVE"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    CORRECTION_ACTIVE = "CORRECTION_ACTIVE"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"
    HUMAN_MERGE_READY = "HUMAN_MERGE_READY"
    BLOCKED = "BLOCKED"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"


class LifecycleTransitionError(ValueError):
    pass


class CoordinatorLifecycle:
    """Coordinator-owned lifecycle; role completion never terminally completes it."""

    _TRANSITIONS = {
        LifecycleState.IMPLEMENTATION_ACTIVE: {LifecycleState.IMPLEMENTATION_READY},
        LifecycleState.IMPLEMENTATION_READY: {LifecycleState.REVIEW_ACTIVE},
        LifecycleState.REVIEW_ACTIVE: {LifecycleState.CHANGES_REQUESTED, LifecycleState.REVIEW_ACCEPTED},
        LifecycleState.CHANGES_REQUESTED: {LifecycleState.CORRECTION_ACTIVE},
        LifecycleState.CORRECTION_ACTIVE: {LifecycleState.IMPLEMENTATION_READY},
        LifecycleState.REVIEW_ACCEPTED: {LifecycleState.HUMAN_MERGE_READY},
    }

    def __init__(self) -> None:
        self.state = LifecycleState.IMPLEMENTATION_ACTIVE
        self._operations: dict[str, LifecycleState] = {}
        self._unknown: tuple[str, LifecycleState, LifecycleState] | None = None

    @staticmethod
    def _valid_operation(operation_id: str) -> bool:
        return bool(re.fullmatch(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", operation_id))

    @staticmethod
    def _ready_evidence(evidence: dict[str, str] | None) -> bool:
        if not isinstance(evidence, dict):
            return False
        sha = evidence.get("commit_sha", "")
        return (
            bool(re.fullmatch(r"[0-9a-f]{40}", sha))
            and bool(evidence.get("pull_request_url"))
            and bool(evidence.get("local_gates"))
            and bool(evidence.get("ci_status"))
        )

    def transition(
        self,
        actor: str,
        next_state: LifecycleState,
        operation_id: str,
        evidence: dict[str, str] | None = None,
    ) -> LifecycleState:
        if actor != "coordinator":
            raise LifecycleTransitionError("Coordinator is the sole lifecycle owner")
        if not self._valid_operation(operation_id):
            raise LifecycleTransitionError("lifecycle transitions require a UUID operation ID")
        prior = self._operations.get(operation_id)
        if prior is not None:
            if prior is not next_state:
                raise LifecycleTransitionError("operation ID cannot be reused for another transition")
            return self.state
        if self.state in {LifecycleState.DELIVERY_UNKNOWN, LifecycleState.BLOCKED, LifecycleState.HUMAN_MERGE_READY}:
            raise LifecycleTransitionError(f"cannot transition from {self.state.value}")
        if next_state == LifecycleState.IMPLEMENTATION_READY and not self._ready_evidence(evidence):
            raise LifecycleTransitionError("IMPLEMENTATION_READY requires commit SHA, PR URL, local gates, and CI status")
        if next_state not in self._TRANSITIONS.get(self.state, set()) and next_state is not LifecycleState.BLOCKED:
            raise LifecycleTransitionError(f"invalid lifecycle transition: {self.state.value} -> {next_state.value}")
        if next_state is LifecycleState.REVIEW_ACTIVE and self.state is not LifecycleState.IMPLEMENTATION_READY:
            raise LifecycleTransitionError("Reviewer activates only from IMPLEMENTATION_READY")
        self._operations[operation_id] = next_state
        self.state = next_state
        return self.state

    def observe_delivery_unknown(self, actor: str, operation_id: str, intended_state: LifecycleState) -> LifecycleState:
        if actor != "coordinator":
            raise LifecycleTransitionError("Coordinator is the sole lifecycle owner")
        if not self._valid_operation(operation_id):
            raise LifecycleTransitionError("lifecycle transitions require a UUID operation ID")
        if self.state in {LifecycleState.BLOCKED, LifecycleState.HUMAN_MERGE_READY}:
            raise LifecycleTransitionError(f"cannot observe transport from {self.state.value}")
        if intended_state not in self._TRANSITIONS.get(self.state, set()):
            raise LifecycleTransitionError("unknown transport target is not a valid next lifecycle state")
        self._unknown = (operation_id, self.state, intended_state)
        self.state = LifecycleState.DELIVERY_UNKNOWN
        return self.state

    def reconcile_delivery(self, actor: str, operation_id: str, applied: bool) -> LifecycleState:
        if actor != "coordinator":
            raise LifecycleTransitionError("Coordinator is the sole lifecycle owner")
        if self._unknown is None or self._unknown[0] != operation_id:
            raise LifecycleTransitionError("exact DELIVERY_UNKNOWN operation must be reconciled")
        _, resume_state, intended_state = self._unknown
        self.state = intended_state if applied else resume_state
        self._unknown = None
        self._operations[operation_id] = self.state
        return self.state


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
        "boolean": isinstance(value, bool),
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
    schema_errors = _validate_schema(value, authoritative_schema, authoritative_schema, "handoff")
    if compatibility_errors or schema_errors or not isinstance(value, dict):
        return compatibility_errors or schema_errors
    return validate_routing(value) + validate_lifecycle_handoff(value)
