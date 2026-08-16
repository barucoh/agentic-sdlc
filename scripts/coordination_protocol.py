"""Reference model for Agentic SDLC cross-task delivery semantics.

This module validates scenarios and documents executable state transitions. It does
not persist transport state or perform external side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import UUID


ROLES = {
    "coordinator",
    "product",
    "architecture",
    "implementation",
    "qa",
    "reviewer",
    "knowledge_steward",
    "human_owner",
}
TERMINAL_STATES = {"completed", "changes_requested", "blocked"}
HANDOFF_FIELDS = {
    "schema_version",
    "operation_id",
    "from_role",
    "to_role",
    "work_item",
    "objective",
    "inputs",
    "constraints",
    "adrs",
    "acceptance_criteria",
    "outputs",
    "dependencies",
    "escalation",
    "evidence",
    "residual_risk",
    "terminal_state",
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

    def observe(self, observation: Observation, authoritative_confirmed: bool = False) -> DeliveryState:
        self.attempt_count += 1
        self.retry_allowed = False
        if observation is Observation.EXPLICIT_NON_DELIVERY:
            self.state = DeliveryState.NOT_DELIVERED
            self.retry_allowed = True
        elif observation is Observation.DELIVERED_AND_ACKNOWLEDGED and authoritative_confirmed:
            self.state = DeliveryState.DELIVERED
            self.side_effect_confirmed = True
        else:
            self.state = DeliveryState.DELIVERY_UNKNOWN
        return self.state

    def reconcile(self, result: Reconciliation) -> DeliveryState:
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
        UUID(operation.operation_id)
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


def validate_handoff(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = HANDOFF_FIELDS - value.keys()
    extra = value.keys() - HANDOFF_FIELDS
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"unexpected fields: {', '.join(sorted(extra))}")
    if value.get("schema_version") != "1.0.0":
        errors.append("schema_version must be 1.0.0")
    try:
        UUID(str(value.get("operation_id", "")))
    except ValueError:
        errors.append("operation_id must be a UUID")
    if value.get("from_role") not in ROLES or value.get("to_role") not in ROLES:
        errors.append("from_role and to_role must be known roles")
    if value.get("terminal_state") not in TERMINAL_STATES:
        errors.append("invalid terminal_state")
    for field in (
        "inputs",
        "constraints",
        "adrs",
        "acceptance_criteria",
        "outputs",
        "dependencies",
        "escalation",
        "evidence",
        "residual_risk",
    ):
        if not isinstance(value.get(field), list) or any(not isinstance(item, str) or not item for item in value.get(field, [])):
            errors.append(f"{field} must be a list of non-empty strings")
    work_item = value.get("work_item")
    if not isinstance(work_item, dict) or not {"repository", "issue_number", "issue_url", "title"}.issubset(work_item):
        errors.append("work_item must identify the authoritative issue")
    return errors
