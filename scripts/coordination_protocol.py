# pleiad:managed runtime/v1
"""Reference model for Pleiad cross-task delivery semantics.

This module validates scenarios and documents executable state transitions. It does
not persist transport state or perform external side effects.
"""

from __future__ import annotations

import json
import re
from uuid import NAMESPACE_URL, uuid5
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


_REPOSITORY_SCHEMA_PATH = Path(__file__).resolve().parents[1] / ".pleiad" / "handoff.schema.json"
_BOOTSTRAP_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "bootstrap-pleiad"
    / "assets"
    / "repository"
    / ".pleiad"
    / "handoff.schema.json"
)
# Installed repositories carry the schema beside this managed runtime.  The
# bootstrap checkout keeps the asset fallback for source-tree validation.
SCHEMA_PATH = _REPOSITORY_SCHEMA_PATH if _REPOSITORY_SCHEMA_PATH.is_file() else _BOOTSTRAP_SCHEMA_PATH
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
    "oneOf",
    "pattern",
    "format",
    "minLength",
    "minItems",
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
TARGET_MODELS = ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
EFFORT_LEVELS = ("Low", "Medium", "High")
ROUTING_POLICY_PATH = SCHEMA_PATH.parents[1] / ".pleiad" / "model-routing.json"
ROUTING_POLICY_SCHEMA_VERSION = 1
DEFAULT_ROUTING_MATRIX = {
    "coordinator": {"gpt-5.6-sol": {"Medium"}},
    "product": {"gpt-5.6-sol": {"Medium"}},
    "architecture": {"gpt-5.6-sol": {"Medium"}},
    "implementation": {
        "gpt-5.6-luna": {"Low"},
        "gpt-5.6-terra": {"Low", "Medium", "High"},
        "gpt-5.6-sol": {"Medium", "High"},
    },
    "qa": {"gpt-5.6-sol": {"Medium", "High"}},
    "reviewer": {"gpt-5.6-sol": {"Medium", "High"}},
    "knowledge_steward": {
        "gpt-5.6-luna": {"Low"},
        "gpt-5.6-sol": {"Medium"},
    },
    "human_owner": {"gpt-5.6-sol": {"Medium"}},
}
DEFAULT_ROLE_DEFAULTS = {
    "coordinator": {"model": "gpt-5.6-sol", "effort": "Medium"},
    "product": {"model": "gpt-5.6-sol", "effort": "Medium"},
    "architecture": {"model": "gpt-5.6-sol", "effort": "Medium"},
    "implementation": {"model": "gpt-5.6-luna", "effort": "Low"},
    "qa": {"model": "gpt-5.6-sol", "effort": "Medium"},
    "reviewer": {"model": "gpt-5.6-sol", "effort": "Medium"},
    "knowledge_steward": {"model": "gpt-5.6-luna", "effort": "Low"},
    "human_owner": {"model": "gpt-5.6-sol", "effort": "Medium"},
}
ROUTING_POLICY_VERSION = "project-owned-v2"
ROUTING_CONFIG_LINES = (
    "routing_policy: project-owned-v2",
    "routing_policy_path: .pleiad/model-routing.json",
    "routing_policy_defaults: embedded-in-scripts/coordination_protocol.py",
)
LIFECYCLE_CONFIG_LINES = (
    "lifecycle_policy: delivery-cell-v1",
    'lifecycle_sequence: "IMPLEMENTATION_ACTIVE->IMPLEMENTATION_READY->QA_PASSED->REVIEW_ACTIVE->CHANGES_REQUESTED->CORRECTION_ACTIVE->IMPLEMENTATION_READY->QA_PASSED->REVIEW_ACTIVE->REVIEW_ACCEPTED->HUMAN_MERGE_READY"',
    "lifecycle_transport_overlay: per-recipient DELIVERY_UNKNOWN",
    "delivery_topology: one-issue-one-cell-one-implementation-worktree-branch-one-pr",
    "qa_workspace_topology: host-provisioned-isolated-clone",
    "qa_workspace_check: scripts/qa_workspace.py",
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


def default_routing_policy() -> dict[str, Any]:
    """Return a detached copy of the safe built-in policy."""

    return {
        "schema_version": ROUTING_POLICY_SCHEMA_VERSION,
        "astra_enabled": True,
        "role_defaults": {role: dict(route) for role, route in DEFAULT_ROLE_DEFAULTS.items()},
        "allowed_routes": {
            role: [
                *[
                    {"model": model, "efforts": sorted(efforts, key=EFFORT_LEVELS.index)}
                    for model, efforts in routes.items()
                ],
                {"model": "gpt-6-astra", "efforts": ["High"]},
            ]
            for role, routes in DEFAULT_ROUTING_MATRIX.items()
        },
    }


def _policy_error(path: str, message: str) -> str:
    return f"routing policy {path} {message}"


def validate_routing_policy(policy: Any) -> list[str]:
    """Validate the project-owned JSON override without relaxing delivery safety."""

    if not isinstance(policy, dict):
        return ["routing policy must be a JSON object"]
    allowed_fields = {"schema_version", "astra_enabled", "role_defaults", "allowed_routes"}
    errors = [_policy_error(key, "is not supported") for key in sorted(set(policy) - allowed_fields)]
    if policy.get("schema_version") != ROUTING_POLICY_SCHEMA_VERSION:
        errors.append(_policy_error("schema_version", f"must equal {ROUTING_POLICY_SCHEMA_VERSION}"))
    if not isinstance(policy.get("astra_enabled"), bool):
        errors.append(_policy_error("astra_enabled", "must be true or false"))
    defaults = policy.get("role_defaults")
    routes = policy.get("allowed_routes")
    if not isinstance(defaults, dict):
        errors.append(_policy_error("role_defaults", "must be an object"))
        defaults = {}
    if not isinstance(routes, dict):
        errors.append(_policy_error("allowed_routes", "must be an object"))
        routes = {}
    supported_roles = set(DEFAULT_ROUTING_MATRIX)
    for role in sorted(supported_roles - set(defaults)):
        errors.append(_policy_error("role_defaults", f"is missing required role {role!r}"))
    for role in sorted(supported_roles - set(routes)):
        errors.append(_policy_error("allowed_routes", f"is missing required role {role!r}"))
    for group_name, group in (("role_defaults", defaults), ("allowed_routes", routes)):
        for role in sorted(set(group) - supported_roles):
            errors.append(_policy_error(f"{group_name}.{role}", "names an unsupported role"))
    parsed_routes: dict[str, set[tuple[str, str]]] = {}
    for role, configured in routes.items():
        if role not in supported_roles:
            continue
        if not isinstance(configured, list) or not configured:
            errors.append(_policy_error(f"allowed_routes.{role}", "must be a non-empty list"))
            continue
        pairs: set[tuple[str, str]] = set()
        for index, item in enumerate(configured):
            path = f"allowed_routes.{role}[{index}]"
            if not isinstance(item, dict) or set(item) != {"model", "efforts"}:
                errors.append(_policy_error(path, "must contain only model and efforts"))
                continue
            model, efforts = item.get("model"), item.get("efforts")
            if model not in TARGET_MODELS:
                errors.append(_policy_error(f"{path}.model", f"is unsupported: {model!r}"))
                continue
            if not isinstance(efforts, list) or not efforts:
                errors.append(_policy_error(f"{path}.efforts", "must be a non-empty list"))
                continue
            for effort in efforts:
                if effort not in EFFORT_LEVELS:
                    errors.append(_policy_error(f"{path}.efforts", f"contains unsupported effort {effort!r}"))
                elif (model, effort) in pairs:
                    errors.append(_policy_error(path, "contains a duplicate model/effort route"))
                else:
                    pairs.add((model, effort))
        parsed_routes[role] = pairs
    for role, configured in defaults.items():
        if role not in supported_roles:
            continue
        path = f"role_defaults.{role}"
        if not isinstance(configured, dict) or set(configured) != {"model", "effort"}:
            errors.append(_policy_error(path, "must contain only model and effort"))
            continue
        model, effort = configured.get("model"), configured.get("effort")
        if model == "gpt-6-astra":
            errors.append(_policy_error(path, "cannot use Astra as a routine default"))
        if (model, effort) not in parsed_routes.get(role, set()):
            errors.append(_policy_error(path, "must be one of that role's allowed routes"))
    return errors


def load_routing_policy(path: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """Load defaults or the complete project-owned replacement policy."""

    path = path or ROUTING_POLICY_PATH
    if not path.exists():
        return default_routing_policy(), []
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"routing policy {path} is not valid JSON: {exc}"]
    errors = validate_routing_policy(policy)
    return (policy if not errors else {}), errors


def policy_route_matrix(policy: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    matrix: dict[str, dict[str, set[str]]] = {}
    for role, routes in policy["allowed_routes"].items():
        matrix[role] = {}
        for route in routes:
            matrix[role].setdefault(route["model"], set()).update(route["efforts"])
    return matrix


def resolve_routing_defaults(value: Any, policy_path: Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Materialize a role's configured default route before any dispatch."""

    if not isinstance(value, dict):
        return None, ["handoff must be an object before routing defaults can be resolved"]
    policy, errors = load_routing_policy(policy_path)
    if errors:
        return None, errors
    resolved = dict(value)
    model_present = "target_model" in resolved
    effort_present = "effort" in resolved
    if model_present != effort_present:
        return None, ["target_model and effort must be supplied together or both resolved from the role default"]
    if not model_present:
        role = resolved.get("to_role")
        default = policy.get("role_defaults", {}).get(role)
        if not isinstance(default, dict):
            return None, [f"routing policy has no default route for role {role!r}"]
        resolved["target_model"] = default["model"]
        resolved["effort"] = default["effort"]
    return resolved, []


def validate_host_availability(value: dict[str, Any], available_routes: dict[str, Any] | None) -> list[str]:
    """Reject a policy-valid route that the actual destination host cannot run."""

    if not isinstance(available_routes, dict):
        return ["host model availability is required before transport"]
    model, effort = value.get("target_model"), value.get("effort")
    available_efforts = available_routes.get(model)
    if not isinstance(available_efforts, (list, tuple, set)) or effort not in available_efforts:
        return [f"requested route {model}/{effort} is unavailable on the destination host; do not substitute a model"]
    return []


def validate_routing(value: dict[str, Any], policy_path: Path | None = None) -> list[str]:
    """Enforce the project-owned routing policy and explicit activation."""

    errors: list[str] = []
    target_role = value.get("to_role")
    model = value.get("target_model")
    effort = value.get("effort")
    mode = value.get("execution_mode")
    sandbox = value.get("sandbox_mode")
    rationale = value.get("rationale", "")
    policy, policy_errors = load_routing_policy(policy_path)
    if policy_errors:
        return policy_errors
    matrix = policy_route_matrix(policy)
    if mode == "ephemeral_research":
        if model not in TARGET_MODELS:
            errors.append(f"ephemeral_research cannot route to target model {model!r}")
        elif model == "gpt-5.6-luna" and effort != "Low":
            errors.append("ephemeral Luna routing is limited to Low effort")
        elif model == "gpt-5.6-terra" and effort != "Low":
            errors.append("ephemeral Terra routing is limited to Low effort")
        elif model == "gpt-5.6-sol" and "exceptional" not in rationale.lower():
            errors.append("ephemeral Sol routing requires an explicit exceptional rationale")
        elif model == "gpt-6-astra":
            errors.append("ephemeral research cannot use Astra")
        if sandbox != "read-only":
            errors.append("ephemeral_research handoffs must be read-only")
    elif mode == "durable":
        if target_role not in matrix:
            return [f"handoff.to_role has no routing policy: {target_role!r}"]
        allowed_models = matrix[target_role]
        if model not in TARGET_MODELS or model not in allowed_models:
            errors.append(f"{target_role} cannot route to target model {model!r}")
        elif effort not in allowed_models[model]:
            errors.append(f"{target_role} cannot use effort {effort!r} with {model}")
        # QA receives an isolated disposable workspace-write task so behavioral
        # tools may create caches/build outputs without touching implementation
        # source, history, or the implementation branch.
        expected_sandbox = "workspace-write" if target_role in {"implementation", "qa", "knowledge_steward"} else "read-only"
        if sandbox != expected_sandbox:
            errors.append(f"durable {target_role} handoff must use sandbox_mode={expected_sandbox!r}")
    else:
        errors.append(f"unknown execution mode: {mode!r}")

    if mode == "durable" and model == "gpt-6-astra":
        escalation = value.get("exceptional_escalation")
        if not policy.get("astra_enabled"):
            errors.append("Astra is disabled by the project routing policy")
        if not isinstance(escalation, dict) or set(escalation) != {"difficulty", "sol_insufficiency"}:
            errors.append("Astra routing requires exceptional_escalation with difficulty and sol_insufficiency")
        elif escalation.get("difficulty") != "exceptional" or not isinstance(escalation.get("sol_insufficiency"), str) or len(escalation["sol_insufficiency"].strip()) < 20:
            errors.append("Astra escalation must declare exceptional difficulty and concretely explain why Sol is insufficient")
    elif value.get("exceptional_escalation") is not None:
        errors.append("exceptional_escalation is only valid for Astra routing")

    if value.get("is_correction"):
        if target_role != "implementation":
            errors.append("corrections must return to the implementation role")
        if mode != "durable":
            errors.append("corrections require durable delivery")
    return errors


def lifecycle_routes(issue: int, current: LifecycleState, nxt: LifecycleState, event: str | None = None) -> tuple[tuple[str, tuple[str, ...], str, tuple[str, ...]], ...]:
    """Authoritative peer routes, including one-event fan-out recipients."""
    if event is None:
        return ()
    pairs = {
        (LifecycleState.IMPLEMENTATION_ACTIVE, LifecycleState.IMPLEMENTATION_READY): ("implementation", ("qa", "reviewer")),
        (LifecycleState.CORRECTION_ACTIVE, LifecycleState.IMPLEMENTATION_READY): ("implementation", ("qa", "reviewer")),
        (LifecycleState.IMPLEMENTATION_READY, LifecycleState.QA_PASSED): ("qa", ("reviewer", "implementation")),
        (LifecycleState.IMPLEMENTATION_READY, LifecycleState.QA_CHANGES_REQUESTED): ("qa", ("reviewer", "implementation")),
        (LifecycleState.QA_PASSED, LifecycleState.REVIEW_ACTIVE): ("qa", ("reviewer",)),
        (LifecycleState.REVIEW_ACTIVE, LifecycleState.CHANGES_REQUESTED): ("reviewer", ("implementation",)),
        (LifecycleState.REVIEW_ACTIVE, LifecycleState.REVIEW_ACCEPTED): ("reviewer", ("reviewer",)),
        (LifecycleState.QA_CHANGES_REQUESTED, LifecycleState.CORRECTION_ACTIVE): ("qa", ("implementation",)),
        (LifecycleState.CHANGES_REQUESTED, LifecycleState.CORRECTION_ACTIVE): ("reviewer", ("implementation",)),
        (LifecycleState.REVIEW_ACCEPTED, LifecycleState.HUMAN_MERGE_READY): ("reviewer", ("coordinator",)),
    }
    if nxt in {LifecycleState.BLOCKED, LifecycleState.ESCALATED}:
        roles = tuple((role, ("coordinator",)) for role in ("implementation", "qa", "reviewer", "coordinator"))
    else:
        pair = pairs.get((current, nxt))
        roles = (pair,) if pair else ()
    if not roles:
        return ()
    events = {
        LifecycleState.IMPLEMENTATION_READY: "IMPLEMENTATION_READY", LifecycleState.QA_PASSED: "QA_PASSED", LifecycleState.QA_CHANGES_REQUESTED: "QA_CHANGES_REQUESTED", LifecycleState.REVIEW_ACTIVE: "REVIEW_ACTIVATE",
        LifecycleState.CHANGES_REQUESTED: "CHANGES_REQUESTED", LifecycleState.CORRECTION_ACTIVE: "CORRECTION_ACTIVATE",
        LifecycleState.REVIEW_ACCEPTED: "REVIEW_ACCEPTED", LifecycleState.HUMAN_MERGE_READY: "DELIVERY_CELL_COMPLETED",
        LifecycleState.BLOCKED: "BLOCKED", LifecycleState.ESCALATED: "ESCALATED",
    }
    if events.get(nxt) != event:
        return ()
    return tuple((source, targets, f"issue-{issue}-{source.replace('_', '-')}", tuple(f"issue-{issue}-{target.replace('_', '-')}" for target in targets)) for source, targets in roles)


def lifecycle_tuple(issue: int, current: LifecycleState, nxt: LifecycleState, event: str | None = None) -> tuple[str, str, str, str] | None:
    """Compatibility view of the first recipient; use lifecycle_routes for fan-out."""
    routes = lifecycle_routes(issue, current, nxt, event)
    if not routes:
        return None
    source, targets, source_key, target_keys = routes[0]
    return source, targets[0], source_key, target_keys[0]


def canonicalize_and_validate_fallback(fallback: Any, operation_id: str, work_item: CanonicalWorkItem | None = None) -> dict[str, str] | None:
    fields = ("repository", "issue_or_pr_url", "operation_id", "objective", "expected_output", "evidence", "next_owner")
    if not isinstance(fallback, dict) or set(fallback) != set(fields) or any(not isinstance(fallback.get(field), str) or not fallback[field].strip() or fallback[field] != fallback[field].strip() for field in fields):
        return None
    result = {field: fallback[field] for field in fields}
    if result["operation_id"] != operation_id or not _valid_repository(result["repository"]):
        return None
    if result["next_owner"] not in {"coordinator", "product", "architecture", "implementation", "qa", "reviewer", "knowledge_steward", "human_owner"}:
        return None
    if not re.fullmatch(r"https://github\.com/" + re.escape(result["repository"]) + r"/(issues|pull)/[1-9][0-9]*", result["issue_or_pr_url"]):
        return None
    if work_item is not None and (result["repository"] != work_item.repository or result["issue_or_pr_url"] not in {work_item.issue_url, work_item.pull_request_url}):
        return None
    return result


def validate_lifecycle_handoff(value: dict[str, Any]) -> list[str]:
    state = value.get("lifecycle_state")
    work_item = value.get("work_item", {})
    evidence = [str(item).lower() for item in value.get("evidence", [])]
    errors: list[str] = []
    try:
        canonical_work_item = CanonicalWorkItem.from_value(work_item)
    except LifecycleTransitionError as exc:
        return [str(exc)]
    issue = work_item.get("issue_number")
    try:
        nxt = LifecycleState(state)
    except ValueError:
        return ["unknown lifecycle state"]
    allowed = [item for current in LifecycleState for item in lifecycle_routes(issue, current, nxt, value.get("lifecycle_event"))]
    if state != LifecycleState.IMPLEMENTATION_ACTIVE.value and not any(
        value.get("from_role") == src and value.get("to_role") == targets[0] and value.get("source_task_key") == source_key and value.get("target_task_key") == target_keys[0]
        for src, targets, source_key, target_keys in allowed
    ):
        errors.append("lifecycle sender, receiver, and logical keys are not an allowed event tuple")
    readiness = value.get("readiness_evidence")
    if state in {LifecycleState.IMPLEMENTATION_READY.value, LifecycleState.REVIEW_ACTIVE.value}:
        if not _valid_readiness_evidence(readiness, work_item):
            errors.append("lifecycle readiness requires typed passed evidence")
        elif readiness.get("commit_sha") != work_item.get("commit_sha") or readiness.get("pull_request_url") != work_item.get("pull_request_url"):
            errors.append("readiness evidence must exactly match canonical work item PR and SHA")
    expected_recipients = next((keys for src, targets, source, keys in allowed if value.get("from_role") == src and value.get("source_task_key") == source and value.get("target_task_key") == keys[0]), ())
    recipients = value.get("recipient_task_keys")
    recipient_ids = value.get("recipient_operation_ids")
    if tuple(recipients or ()) != expected_recipients or not isinstance(recipient_ids, list) or len(recipient_ids) != len(expected_recipients) or len(set(recipient_ids)) != len(recipient_ids) or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", item) for item in recipient_ids):
        errors.append("lifecycle handoff must carry exact unique per-recipient delivery operation IDs")
    if state == LifecycleState.IMPLEMENTATION_READY.value:
        if not re.fullmatch(r"[0-9a-f]{40}", str(work_item.get("commit_sha") or "")):
            errors.append("IMPLEMENTATION_READY requires an exact commit SHA")
        if not work_item.get("pull_request_url"):
            errors.append("IMPLEMENTATION_READY requires a pull request URL")
    if state == LifecycleState.REVIEW_ACTIVE.value:
        if value.get("to_role") != "reviewer":
            errors.append("REVIEW_ACTIVE handoffs must explicitly activate Reviewer")
    if state == LifecycleState.CORRECTION_ACTIVE.value:
        if value.get("to_role") != "implementation" or not value.get("is_correction"):
            errors.append("CORRECTION_ACTIVE must return to the same Implementation task")
    if state == LifecycleState.HUMAN_MERGE_READY.value and value.get("terminal_state") != "completed":
        errors.append("HUMAN_MERGE_READY requires completed non-human gates")
    if state in {LifecycleState.IMPLEMENTATION_READY.value, LifecycleState.QA_PASSED.value, LifecycleState.REVIEW_ACCEPTED.value, LifecycleState.HUMAN_MERGE_READY.value} and not _valid_delivery_evidence(readiness, work_item, nxt, require_recorded=False):
        errors.append("delivery-cell completion requires exact typed implementation, QA, and review evidence")
    if state == LifecycleState.BLOCKED.value:
        fallback = value.get("blocked_fallback")
        if canonicalize_and_validate_fallback(fallback, value.get("operation_id", ""), canonical_work_item) is None:
            errors.append("BLOCKED requires a reconstructible fallback")
    return errors


def _valid_repository(repository: Any) -> bool:
    return isinstance(repository, str) and repository == repository.strip() and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository))


_READINESS_FIELDS = {"commit_sha", "pull_request_url", "local_gates", "ci_status", "required_checks"}
_READINESS_EVIDENCE_FIELDS = _READINESS_FIELDS | {"implementation_evidence", "qa_evidence", "review_evidence"}


def _valid_check_list(checks: Any) -> bool:
    if not isinstance(checks, list) or not checks:
        return False
    if any(not isinstance(check, dict) or set(check) != {"name", "status"} or not isinstance(check["name"], str) or not check["name"].strip() or check["status"] != "passed" for check in checks):
        return False
    names = [check["name"] for check in checks]
    return len(names) == len(set(names))


def _valid_common_evidence(evidence: Any, work_item: dict[str, Any] | None = None) -> bool:
    if not isinstance(evidence, dict) or set(evidence) != _READINESS_FIELDS:
        return False
    sha = evidence.get("commit_sha")
    url = evidence.get("pull_request_url")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return False
    if not isinstance(url, str) or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*", url):
        return False
    if evidence.get("local_gates") != "passed" or evidence.get("ci_status") != "passed" or not _valid_check_list(evidence.get("required_checks")):
        return False
    if work_item is not None:
        if not _valid_repository(work_item.get("repository")) or (work_item.get("commit_sha") is not None and sha != work_item.get("commit_sha")) or (work_item.get("pull_request_url") is not None and url != work_item.get("pull_request_url")):
            return False
        if not url.startswith(f"https://github.com/{work_item['repository']}/pull/"):
            return False
    return True


def _valid_readiness_evidence(evidence: Any, work_item: dict[str, Any] | None = None) -> bool:
    if not isinstance(evidence, dict) or not _READINESS_FIELDS.issubset(evidence) or not set(evidence).issubset(_READINESS_EVIDENCE_FIELDS):
        return False
    if not _valid_common_evidence({key: evidence[key] for key in _READINESS_FIELDS}, work_item):
        return False
    for field, role in (("implementation_evidence", "implementation"), ("qa_evidence", "qa"), ("review_evidence", "reviewer")):
        if field in evidence and not _valid_role_evidence(evidence[field], role, work_item):
            return False
    return True


def _valid_role_evidence(value: Any, role: str, work_item: dict[str, Any] | None) -> bool:
    """Validate a role-owned, exact-artifact evidence object.

    Role evidence deliberately repeats the authoritative artifact and gates.  A
    prose URL is not sufficient authority for a delivery-cell decision.
    """
    fields = {"role", "pull_request_url", "commit_sha", "local_gates", "ci_status", "required_checks"}
    return (
        isinstance(value, dict)
        and set(value) == fields
        and value.get("role") == role
        and _valid_common_evidence({key: value[key] for key in _READINESS_FIELDS}, work_item)
    )


def _valid_delivery_evidence(
    evidence: Any,
    work_item: dict[str, Any] | None,
    state: "LifecycleState",
    *,
    recorded_implementation: dict[str, Any] | None = None,
    recorded_qa: dict[str, Any] | None = None,
    recorded_review: dict[str, Any] | None = None,
    require_recorded: bool = True,
) -> bool:
    """Require role-owned evidence appropriate to the destination state."""
    if not isinstance(evidence, dict) or not _valid_readiness_evidence(evidence, work_item):
        return False
    implementation = evidence.get("implementation_evidence")
    qa = evidence.get("qa_evidence")
    reviewer = evidence.get("review_evidence")
    if state is LifecycleState.IMPLEMENTATION_READY:
        return _valid_role_evidence(implementation, "implementation", work_item)
    if state is LifecycleState.QA_PASSED:
        return _valid_role_evidence(qa, "qa", work_item)
    if state is LifecycleState.REVIEW_ACCEPTED:
        qa_authority = recorded_qa if require_recorded else qa
        return _valid_role_evidence(qa_authority, "qa", work_item) and _valid_role_evidence(reviewer, "reviewer", work_item)
    if state is LifecycleState.HUMAN_MERGE_READY:
        return (
            _valid_role_evidence(implementation, "implementation", work_item)
            and _valid_role_evidence(qa, "qa", work_item)
            and _valid_role_evidence(reviewer, "reviewer", work_item)
            and (not require_recorded or _valid_role_evidence(recorded_implementation, "implementation", work_item))
            and (not require_recorded or _valid_role_evidence(recorded_qa, "qa", work_item))
            and (not require_recorded or _valid_role_evidence(recorded_review, "reviewer", work_item))
        )
    return True


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
    QA_PASSED = "QA_PASSED"
    QA_CHANGES_REQUESTED = "QA_CHANGES_REQUESTED"
    REVIEW_ACTIVE = "REVIEW_ACTIVE"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    CORRECTION_ACTIVE = "CORRECTION_ACTIVE"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"
    HUMAN_MERGE_READY = "HUMAN_MERGE_READY"
    ESCALATED = "ESCALATED"
    BLOCKED = "BLOCKED"


class LifecycleTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalWorkItem:
    repository: str
    issue_number: int
    issue_url: str
    pull_request_url: str | None = None
    commit_sha: str | None = None

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> "CanonicalWorkItem":
        if not isinstance(value, dict) or not _valid_repository(value.get("repository")) or not isinstance(value.get("issue_number"), int) or value["issue_number"] < 1:
            raise LifecycleTransitionError("CoordinatorLifecycle requires a canonical work item")
        issue_url = value.get("issue_url")
        if issue_url != f"https://github.com/{value['repository']}/issues/{value['issue_number']}":
            raise LifecycleTransitionError("canonical work item requires its authoritative GitHub issue URL")
        pr = value.get("pull_request_url")
        sha = value.get("commit_sha")
        if (pr is None) != (sha is None):
            raise LifecycleTransitionError("canonical work item requires PR URL and commit SHA together")
        if pr is not None and not re.fullmatch(r"https://github\.com/" + re.escape(value["repository"]) + r"/pull/[1-9][0-9]*", pr):
            raise LifecycleTransitionError("canonical work item has an invalid PR URL")
        if sha is not None and not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise LifecycleTransitionError("canonical work item has an invalid commit SHA")
        return cls(value["repository"], value["issue_number"], issue_url, pr, sha)

    def as_dict(self) -> dict[str, Any]:
        return {"repository": self.repository, "issue_number": self.issue_number, "issue_url": self.issue_url, "pull_request_url": self.pull_request_url, "commit_sha": self.commit_sha}


@dataclass(frozen=True)
class LifecycleOperationIntent:
    """Single canonical, immutable-in-process snapshot for a lifecycle action.

    This is an in-memory integrity boundary, not a cryptographic tamper-proof
    store: a caller with arbitrary process-memory access can replace objects.
    Reconciliation nevertheless revalidates every semantic prerequisite before
    making a lifecycle or retryability change.
    """

    operation_id: str
    from_state: LifecycleState
    next_state: LifecycleState
    source_task_key: str
    target_task_key: str
    event: str
    readiness_evidence: str
    fallback: str | None
    artifact: tuple[str, str] | None
    correction_checkpoint: int | None
    recipient_task_keys: tuple[str, ...]
    recipient_operation_ids: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactBindingIntent:
    operation_id: str
    work_item: CanonicalWorkItem
    pull_request_url: str
    commit_sha: str


@dataclass(frozen=True)
class ChildDeliveryIdentity:
    operation_id: str
    parent_operation_id: str
    recipient_task_key: str
    recipient_role: str


@dataclass
class ChildDeliveryOperation:
    """One recipient wake-up in the unified operation UUID namespace.

    The parent lifecycle intent applies its state exactly once.  These child
    records state only transport to one exact recipient and can be observed,
    reconciled, and retried without replaying the parent or a sibling.
    """

    identity: ChildDeliveryIdentity
    state: DeliveryState = DeliveryState.PENDING
    attempt_count: int = 1
    retry_allowed: bool = False
    reconciliation_count: int = 0
    last_reconciliation: Reconciliation | None = None
    fallback: str | None = None

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "identity" and hasattr(self, "identity"):
            raise LifecycleTransitionError("child delivery identity is immutable")
        super().__setattr__(name, value)

    @property
    def operation_id(self) -> str:
        return self.identity.operation_id

    @property
    def parent_operation_id(self) -> str:
        return self.identity.parent_operation_id

    @property
    def recipient_task_key(self) -> str:
        return self.identity.recipient_task_key

    @property
    def recipient_role(self) -> str:
        return self.identity.recipient_role

    def unknown(self) -> None:
        if self.state is not DeliveryState.PENDING:
            raise LifecycleTransitionError("only the current pending child attempt may become uncertain")
        self.state = DeliveryState.DELIVERY_UNKNOWN
        self.retry_allowed = False

    def reconcile(self, result: Reconciliation) -> None:
        if self.state is not DeliveryState.DELIVERY_UNKNOWN:
            raise LifecycleTransitionError("only an exact unknown child delivery can be reconciled")
        self.reconciliation_count += 1
        self.last_reconciliation = result
        if result is Reconciliation.APPLIED:
            self.state = DeliveryState.DELIVERED
            self.retry_allowed = False
        elif result is Reconciliation.ABSENT:
            self.state = DeliveryState.NOT_DELIVERED
            self.retry_allowed = True
        else:
            self.state = DeliveryState.DELIVERY_UNKNOWN
            self.retry_allowed = False

    def retry(self) -> None:
        if self.state is not DeliveryState.NOT_DELIVERED or not self.retry_allowed:
            raise LifecycleTransitionError("child delivery is not retryable")
        self.state = DeliveryState.PENDING
        self.retry_allowed = False
        self.attempt_count += 1


class CoordinatorLifecycle:
    """Delivery-cell lifecycle with Coordinator supervision, not message relaying.

    The `actor` for a normal transition or unknown observation is the peer that
    owns the transition's source logical key.  Coordinator reconciles uncertain
    transport and receives the terminal cell result, but is deliberately not an
    alternate sender for routine Implementation, QA, or Reviewer handoffs.
    """

    _TRANSITIONS = {
        LifecycleState.IMPLEMENTATION_ACTIVE: {LifecycleState.IMPLEMENTATION_READY},
        LifecycleState.IMPLEMENTATION_READY: {LifecycleState.QA_PASSED, LifecycleState.QA_CHANGES_REQUESTED},
        LifecycleState.QA_PASSED: {LifecycleState.REVIEW_ACTIVE},
        LifecycleState.REVIEW_ACTIVE: {LifecycleState.CHANGES_REQUESTED, LifecycleState.REVIEW_ACCEPTED},
        LifecycleState.CHANGES_REQUESTED: {LifecycleState.CORRECTION_ACTIVE},
        LifecycleState.QA_CHANGES_REQUESTED: {LifecycleState.CORRECTION_ACTIVE},
        LifecycleState.CORRECTION_ACTIVE: {LifecycleState.IMPLEMENTATION_READY},
        LifecycleState.REVIEW_ACCEPTED: {LifecycleState.HUMAN_MERGE_READY},
    }

    def __init__(self, work_item: dict[str, Any]) -> None:
        self.state = LifecycleState.IMPLEMENTATION_ACTIVE
        self.work_item = CanonicalWorkItem.from_value(work_item)
        self.issue_number = self.work_item.issue_number
        self.coordinator_key = f"issue-{self.issue_number}-coordinator"
        self.implementation_key = f"issue-{self.issue_number}-implementation"
        self.qa_key = f"issue-{self.issue_number}-qa"
        self.reviewer_key = f"issue-{self.issue_number}-reviewer"
        self._artifact: tuple[str, str, str] | None = None
        self._artifact_revisions: list[tuple[str, str, str]] = []
        self._correction_checkpoint: int | None = None
        self._operations: dict[str, LifecycleOperationIntent | ArtifactBindingIntent | ChildDeliveryOperation] = {}
        self._retryable_operations: set[str] = set()
        self._terminal_operations: set[str] = set()
        self._unknown: set[str] = set()
        self._implementation_evidence: dict[str, Any] | None = None
        self._qa_evidence: dict[str, Any] | None = None
        self._review_evidence: dict[str, Any] | None = None

    @staticmethod
    def _valid_operation(operation_id: str) -> bool:
        return bool(re.fullmatch(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", operation_id))

    @staticmethod
    def _ready_evidence(evidence: dict[str, Any] | None, work_item: dict[str, Any] | None = None) -> bool:
        return _valid_readiness_evidence(evidence, work_item)

    def _direction(self, current: LifecycleState, nxt: LifecycleState, source: str, target: str, event: str | None = None) -> bool:
        allowed = lifecycle_tuple(self.issue_number, current, nxt, event)
        return any(source == source_key and target in target_keys for _, _, source_key, target_keys in lifecycle_routes(self.issue_number, current, nxt, event))

    @staticmethod
    def _role_for_task_key(task_key: str) -> str | None:
        match = re.fullmatch(r"issue-[1-9][0-9]*-([a-z][a-z0-9-]*)", task_key)
        return match.group(1).replace("-", "_") if match else None

    def _require_peer_actor(self, actor: str, source_task_key: str) -> None:
        if actor != self._role_for_task_key(source_task_key):
            raise LifecycleTransitionError("delivery-cell transition actor must own the source logical task key")

    def bind_delivery_artifact(self, operation_id: str, pull_request_url: str | None, commit_sha: str | None) -> None:
        """Atomically and immutably bind the PR/SHA authority for readiness."""
        if not self._valid_operation(operation_id) or not isinstance(pull_request_url, str) or not isinstance(commit_sha, str):
            raise LifecycleTransitionError("artifact binding requires a UUID, exact PR URL, and exact SHA")
        if not re.fullmatch(r"https://github\.com/" + re.escape(self.work_item.repository) + r"/pull/[1-9][0-9]*", pull_request_url) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
            raise LifecycleTransitionError("artifact binding must match the canonical repository")
        intent = ArtifactBindingIntent(operation_id, self.work_item, pull_request_url, commit_sha)
        prior = self._operations.get(operation_id)
        if prior is not None:
            if prior == intent and operation_id in self._terminal_operations:
                return
            raise LifecycleTransitionError("operation UUID is already bound to another immutable intent")
        if self._unknown:
            raise LifecycleTransitionError("cannot bind an artifact while delivery reconciliation is pending")
        if self.state in {LifecycleState.BLOCKED, LifecycleState.HUMAN_MERGE_READY}:
            raise LifecycleTransitionError("cannot bind an artifact after terminal lifecycle state")
        if self._artifact is not None and self.state is not LifecycleState.CORRECTION_ACTIVE:
            raise LifecycleTransitionError("delivery artifact is already immutably bound")
        if self._artifact is None and self.state is not LifecycleState.IMPLEMENTATION_ACTIVE:
            raise LifecycleTransitionError("initial artifact binding is allowed only before implementation readiness")
        if self._artifact_revisions:
            original_pr = self._artifact_revisions[0][0]
            prior_shas = {revision[1] for revision in self._artifact_revisions}
            if pull_request_url != original_pr:
                raise LifecycleTransitionError("artifact revisions must retain the original pull request")
            if commit_sha in prior_shas:
                raise LifecycleTransitionError("artifact revisions require a new, never-before-used SHA")
        if self.work_item.pull_request_url is not None and (pull_request_url != self.work_item.pull_request_url or commit_sha != self.work_item.commit_sha):
            raise LifecycleTransitionError("binding must exactly match prebound canonical artifact")
        self._artifact = (pull_request_url, commit_sha, operation_id)
        self._artifact_revisions.append(self._artifact)
        self._operations[operation_id] = intent
        self._terminal_operations.add(operation_id)

    def _bound_ready_evidence(self, evidence: dict[str, Any] | None) -> bool:
        return bool(self._artifact and self._ready_evidence(evidence, {**self.work_item.as_dict(), "pull_request_url": self._artifact[0], "commit_sha": self._artifact[1]}))

    @staticmethod
    def _event_for(next_state: LifecycleState) -> str | None:
        return {
            LifecycleState.IMPLEMENTATION_READY: "IMPLEMENTATION_READY",
            LifecycleState.QA_PASSED: "QA_PASSED",
            LifecycleState.QA_CHANGES_REQUESTED: "QA_CHANGES_REQUESTED",
            LifecycleState.REVIEW_ACTIVE: "REVIEW_ACTIVATE",
            LifecycleState.CHANGES_REQUESTED: "CHANGES_REQUESTED",
            LifecycleState.CORRECTION_ACTIVE: "CORRECTION_ACTIVATE",
            LifecycleState.REVIEW_ACCEPTED: "REVIEW_ACCEPTED",
            LifecycleState.HUMAN_MERGE_READY: "DELIVERY_CELL_COMPLETED",
            LifecycleState.BLOCKED: "BLOCKED",
            LifecycleState.ESCALATED: "ESCALATED",
        }.get(next_state)

    def _build_transition_intent(
        self,
        operation_id: str,
        next_state: LifecycleState,
        source_task_key: str,
        target_task_key: str,
        event: str,
        evidence: dict[str, Any] | None,
        fallback: dict[str, Any] | None,
        recipient_operation_ids: dict[str, str] | None,
    ) -> LifecycleOperationIntent:
        artifact = self._artifact[:2] if self._artifact else None
        route = next((item for item in lifecycle_routes(self.issue_number, self.state, next_state, event) if item[2] == source_task_key and target_task_key in item[3]), None)
        recipients = route[3] if route else ()
        if recipient_operation_ids is None:
            recipient_operation_ids = {key: str(uuid5(NAMESPACE_URL, f"{operation_id}:{key}")) for key in recipients}
        if tuple(recipient_operation_ids) != recipients or len(set(recipient_operation_ids.values())) != len(recipients) or any(not self._valid_operation(item) or item == operation_id for item in recipient_operation_ids.values()):
            raise LifecycleTransitionError("lifecycle operation requires exact unique per-recipient delivery UUIDs")
        return LifecycleOperationIntent(
            operation_id,
            self.state,
            next_state,
            source_task_key,
            target_task_key,
            event,
            json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            json.dumps(fallback, sort_keys=True, separators=(",", ":")) if fallback is not None else None,
            artifact,
            len(self._artifact_revisions) if next_state is LifecycleState.CORRECTION_ACTIVE else None,
            recipients,
            tuple(recipient_operation_ids[key] for key in recipients),
        )

    @staticmethod
    def _decode_canonical_json(value: Any) -> Any:
        if not isinstance(value, str):
            raise LifecycleTransitionError("operation intent payload is malformed")
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as error:
            raise LifecycleTransitionError("operation intent payload is malformed") from error
        if json.dumps(decoded, sort_keys=True, separators=(",", ":")) != value:
            raise LifecycleTransitionError("operation intent payload is not canonical")
        return decoded

    def _validate_transition_intent(self, intent: LifecycleOperationIntent, *, require_prerequisites: bool = True) -> dict[str, Any] | None:
        if not self._valid_operation(intent.operation_id) or type(intent.from_state) is not LifecycleState or type(intent.next_state) is not LifecycleState:
            raise LifecycleTransitionError("operation intent has an invalid lifecycle identity")
        if not all(isinstance(value, str) for value in (intent.source_task_key, intent.target_task_key, intent.event)):
            raise LifecycleTransitionError("operation intent has malformed lifecycle routing")
        if intent.from_state in {LifecycleState.BLOCKED, LifecycleState.HUMAN_MERGE_READY}:
            raise LifecycleTransitionError("operation intent has an invalid source lifecycle state")
        if self.state is not intent.from_state:
            raise LifecycleTransitionError("operation intent no longer matches the current lifecycle state")
        if intent.next_state not in self._TRANSITIONS.get(intent.from_state, set()) and intent.next_state not in {LifecycleState.BLOCKED, LifecycleState.ESCALATED}:
            raise LifecycleTransitionError("operation intent has an invalid lifecycle transition")
        if not self._direction(intent.from_state, intent.next_state, intent.source_task_key, intent.target_task_key, intent.event):
            raise LifecycleTransitionError("operation intent has an unauthorized lifecycle direction")
        route = next((item for item in lifecycle_routes(self.issue_number, intent.from_state, intent.next_state, intent.event) if item[2] == intent.source_task_key and intent.target_task_key in item[3]), None)
        if route is None or intent.recipient_task_keys != route[3] or len(intent.recipient_operation_ids) != len(route[3]) or len(set(intent.recipient_operation_ids)) != len(route[3]) or any(not self._valid_operation(item) or item == intent.operation_id for item in intent.recipient_operation_ids):
            raise LifecycleTransitionError("operation intent has invalid per-recipient delivery records")
        current_artifact = self._artifact[:2] if self._artifact else None
        if intent.artifact != current_artifact:
            raise LifecycleTransitionError("operation intent no longer matches the authoritative artifact")
        evidence = self._decode_canonical_json(intent.readiness_evidence)
        if evidence is not None and not isinstance(evidence, dict):
            raise LifecycleTransitionError("operation intent has malformed readiness evidence")
        fallback: dict[str, Any] | None = None
        if intent.next_state in {LifecycleState.BLOCKED, LifecycleState.ESCALATED}:
            if intent.fallback is None:
                raise LifecycleTransitionError("BLOCKED operation intent requires a reconstructible fallback")
            fallback = self._decode_canonical_json(intent.fallback)
            canonical = canonicalize_and_validate_fallback(fallback, intent.operation_id, self.work_item)
            if canonical is None or fallback != canonical:
                raise LifecycleTransitionError("BLOCKED operation intent has an invalid fallback")
        elif intent.fallback is not None:
            raise LifecycleTransitionError("non-escalation operation intent cannot include a fallback")
        if require_prerequisites and intent.next_state is LifecycleState.CORRECTION_ACTIVE:
            # Artifact binding is prohibited while DELIVERY_UNKNOWN is pending, so
            # the append-only revision count is stable through exact reconciliation.
            if type(intent.correction_checkpoint) is not int or intent.correction_checkpoint < 0 or intent.correction_checkpoint != len(self._artifact_revisions):
                raise LifecycleTransitionError("correction checkpoint is missing, malformed, or not authoritative")
        elif require_prerequisites and intent.correction_checkpoint is not None:
            raise LifecycleTransitionError("non-correction operation intent has a checkpoint")
        artifact_work_item = {**self.work_item.as_dict(), "pull_request_url": self._artifact[0] if self._artifact else None, "commit_sha": self._artifact[1] if self._artifact else None}
        if require_prerequisites and intent.next_state in {LifecycleState.IMPLEMENTATION_READY, LifecycleState.REVIEW_ACTIVE} and not self._bound_ready_evidence(evidence):
            raise LifecycleTransitionError("operation intent requires canonical passed readiness evidence")
        if require_prerequisites and intent.next_state in {LifecycleState.IMPLEMENTATION_READY, LifecycleState.QA_PASSED, LifecycleState.REVIEW_ACCEPTED, LifecycleState.HUMAN_MERGE_READY} and not _valid_delivery_evidence(
            evidence,
            artifact_work_item,
            intent.next_state,
            recorded_implementation=self._implementation_evidence,
            recorded_qa=self._qa_evidence,
            recorded_review=self._review_evidence,
        ):
            raise LifecycleTransitionError("terminal delivery-cell operation requires exact typed QA and review evidence")
        if require_prerequisites and intent.next_state is LifecycleState.IMPLEMENTATION_READY and intent.from_state is LifecycleState.CORRECTION_ACTIVE and (self._correction_checkpoint is None or len(self._artifact_revisions) <= self._correction_checkpoint):
            raise LifecycleTransitionError("correction readiness requires a later artifact revision")
        return evidence

    def _apply_transition_intent(self, intent: LifecycleOperationIntent) -> LifecycleState:
        evidence = self._validate_transition_intent(intent)
        if intent.next_state is LifecycleState.CORRECTION_ACTIVE:
            self._correction_checkpoint = intent.correction_checkpoint
        if intent.next_state is LifecycleState.IMPLEMENTATION_READY:
            self._implementation_evidence = evidence.get("implementation_evidence") if evidence else None
        if intent.next_state is LifecycleState.QA_PASSED:
            self._qa_evidence = evidence.get("qa_evidence") if evidence else None
        if intent.next_state is LifecycleState.REVIEW_ACCEPTED:
            self._review_evidence = evidence.get("review_evidence") if evidence else None
        self.state = intent.next_state
        self._retryable_operations.discard(intent.operation_id)
        if intent.next_state is LifecycleState.HUMAN_MERGE_READY:
            self._terminal_operations.add(intent.operation_id)
        return self.state

    def _register_child_deliveries(self, intent: LifecycleOperationIntent) -> None:
        """Atomically register every fan-out wake-up in the same UUID registry."""
        children = tuple(zip(intent.recipient_task_keys, intent.recipient_operation_ids, strict=True))
        if any(operation_id in self._operations for _, operation_id in children):
            raise LifecycleTransitionError("recipient delivery UUID collides with an existing operation")
        for recipient_key, operation_id in children:
            role = self._role_for_task_key(recipient_key)
            if role is None:
                raise LifecycleTransitionError("recipient delivery requires a canonical logical task key")
            self._operations[operation_id] = ChildDeliveryOperation(
                identity=ChildDeliveryIdentity(
                    operation_id=operation_id,
                    parent_operation_id=intent.operation_id,
                    recipient_task_key=recipient_key,
                    recipient_role=role,
                ),
            )

    def transition(
        self,
        actor: str,
        next_state: LifecycleState,
        operation_id: str,
        evidence: dict[str, Any] | None = None,
        *,
        source_task_key: str | None = None,
        target_task_key: str | None = None,
        event: str | None = None,
        fallback: dict[str, Any] | None = None,
        recipient_operation_ids: dict[str, str] | None = None,
    ) -> LifecycleState:
        if not self._valid_operation(operation_id):
            raise LifecycleTransitionError("lifecycle transitions require a UUID operation ID")
        if next_state in {LifecycleState.BLOCKED, LifecycleState.ESCALATED}:
            fallback = canonicalize_and_validate_fallback(fallback, operation_id, self.work_item)
            if fallback is None:
                raise LifecycleTransitionError("BLOCKED requires a valid reconstructible fallback")
        if (source_task_key is None) != (target_task_key is None):
            raise LifecycleTransitionError("source and target logical task keys must be supplied together")
        if source_task_key is None:
            default_event = self._event_for(next_state)
            tuple_ = lifecycle_tuple(self.issue_number, self.state, next_state, event or default_event)
            source_task_key, target_task_key = tuple_[2:] if tuple_ else (self.coordinator_key, self.coordinator_key)
        event = event or self._event_for(next_state)
        if event is None:
            raise LifecycleTransitionError("lifecycle event is required")
        self._require_peer_actor(actor, source_task_key)
        intent = self._build_transition_intent(operation_id, next_state, source_task_key, target_task_key, event, evidence, fallback, recipient_operation_ids)
        self._validate_transition_intent(intent)
        prior = self._operations.get(operation_id)
        if prior is not None:
            if not isinstance(prior, LifecycleOperationIntent) or prior != intent:
                raise LifecycleTransitionError("operation ID cannot be reused for another transition")
            if operation_id in self._retryable_operations:
                return self._apply_transition_intent(prior)
            if operation_id in self._terminal_operations:
                return self.state
            return self.state
        if any(child_id in self._operations for child_id in intent.recipient_operation_ids):
            raise LifecycleTransitionError("recipient delivery UUID collides with an existing operation")
        self._operations[operation_id] = intent
        self._register_child_deliveries(intent)
        return self._apply_transition_intent(intent)

    def observe_delivery_unknown(self, actor: str, operation_id: str, intended_state: LifecycleState | None = None, evidence: dict[str, Any] | None = None, *, target_task_key: str | None = None, fallback: dict[str, Any] | None = None, **_ignored: Any) -> LifecycleState:
        """Mark one existing recipient wake-up uncertain without changing lifecycle state.

        `intended_state` and `evidence` are accepted only for an explicit
        backwards-compatible call shape and are never used to create a generic
        lifecycle uncertainty transition.  The child record is the exact
        transport authority; GitHub evidence remains the durable fallback.
        """
        child = self._operations.get(operation_id)
        if not isinstance(child, ChildDeliveryOperation):
            raise LifecycleTransitionError("DELIVERY_UNKNOWN requires an existing exact recipient delivery UUID")
        parent = self._operations.get(child.parent_operation_id)
        if not isinstance(parent, LifecycleOperationIntent):
            raise LifecycleTransitionError("recipient delivery has no authoritative parent lifecycle operation")
        if actor != self._role_for_task_key(parent.source_task_key):
            raise LifecycleTransitionError("only the parent sender may report its recipient delivery unknown")
        if target_task_key is None or target_task_key != child.recipient_task_key:
            raise LifecycleTransitionError("delivery unknown requires its exact recipient logical task key")
        if intended_state is not None and intended_state is not parent.next_state:
            raise LifecycleTransitionError("delivery unknown cannot retarget its parent lifecycle event")
        canonical_fallback = canonicalize_and_validate_fallback(fallback, operation_id, self.work_item) if fallback is not None else None
        if fallback is not None and canonical_fallback is None:
            raise LifecycleTransitionError("delivery unknown fallback is malformed")
        if child.recipient_role == "coordinator" and canonical_fallback is None:
            raise LifecycleTransitionError("delivery unknown to Coordinator requires a copy-paste fallback")
        child.unknown()
        child.fallback = json.dumps(canonical_fallback, sort_keys=True, separators=(",", ":")) if canonical_fallback else None
        self._unknown.add(operation_id)
        return self.state

    def retry_delivery(self, actor: str, operation_id: str) -> DeliveryState:
        child = self._operations.get(operation_id)
        if not isinstance(child, ChildDeliveryOperation):
            raise LifecycleTransitionError("retry requires an exact child delivery UUID")
        parent = self._operations.get(child.parent_operation_id)
        if not isinstance(parent, LifecycleOperationIntent) or actor != self._role_for_task_key(parent.source_task_key):
            raise LifecycleTransitionError("only the parent sender may retry a recipient delivery")
        child.retry()
        return child.state

    def reconcile_delivery(self, actor: str, operation_id: str, applied: bool | Reconciliation) -> LifecycleState:
        """Reconcile one exact child wake-up; never replay its parent transition."""
        if actor != "coordinator":
            raise LifecycleTransitionError("Coordinator supervises delivery reconciliation")
        child = self._operations.get(operation_id)
        if not isinstance(child, ChildDeliveryOperation) or operation_id not in self._unknown:
            raise LifecycleTransitionError("exact unknown recipient delivery must be reconciled")
        result = applied if isinstance(applied, Reconciliation) else (Reconciliation.APPLIED if applied else Reconciliation.ABSENT)
        child.reconcile(result)
        if result is not Reconciliation.UNAVAILABLE:
            self._unknown.discard(operation_id)
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
        if self.state is DeliveryState.NOT_DELIVERED:
            raise LifecycleTransitionError("an absent delivery requires an exact retry before another attempt")
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
        if self.state is DeliveryState.NOT_DELIVERED:
            raise LifecycleTransitionError("an absent delivery cannot be applied without an exact retry")
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

    def retry(self) -> DeliveryState:
        if self.state is not DeliveryState.NOT_DELIVERED or not self.retry_allowed:
            raise LifecycleTransitionError("delivery is not retryable")
        self.state = DeliveryState.PENDING
        self.retry_allowed = False
        self.attempt_count += 1
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
    for index, child in enumerate(schema.get("oneOf", [])):
        if isinstance(child, dict):
            errors.extend(_schema_support_errors(child, f"{path}.oneOf[{index}]"))
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

    if "oneOf" in schema:
        matches = sum(not _validate_schema(value, option, root, path) for option in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path} must match exactly one schema alternative")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path} must contain at least {schema['minItems']} item(s)")
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


def validate_handoff(value: Any, schema: dict[str, Any] | None = None, policy_path: Path | None = None) -> list[str]:
    authoritative_schema = schema or json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    compatibility_errors = _schema_support_errors(authoritative_schema)
    schema_errors = _validate_schema(value, authoritative_schema, authoritative_schema, "handoff")
    if compatibility_errors or schema_errors or not isinstance(value, dict):
        return compatibility_errors or schema_errors
    return validate_routing(value, policy_path) + validate_lifecycle_handoff(value)


def pre_dispatch_handoff(
    value: Any,
    dispatch: Callable[[dict[str, Any]], Any],
    *,
    action: str,
    available_routes: dict[str, Any] | None = None,
    policy_path: Path | None = None,
) -> Any:
    """Validate the complete envelope before an issue-backed side effect.

    Task creation and cross-task sends share this seam.  The callback is never
    invoked when schema, routing, or lifecycle validation fails, which keeps
    transport adapters from becoming a second handoff authority.
    """

    resolved, resolution_errors = resolve_routing_defaults(value, policy_path)
    errors = resolution_errors or validate_handoff(resolved, policy_path=policy_path)
    if not errors and resolved is not None:
        errors = validate_host_availability(resolved, available_routes)
    if errors:
        detail = "; ".join(errors[:4])
        if len(errors) > 4:
            detail += f"; and {len(errors) - 4} more"
        raise LifecycleTransitionError(f"{action} blocked by invalid handoff: {detail}")
    return dispatch(resolved)


def dispatch_issue_task(value: Any, create_task: Callable[[dict[str, Any]], Any], *, available_routes: dict[str, Any] | None = None, policy_path: Path | None = None) -> Any:
    """Canonical pre-dispatch path for creating a durable role task."""

    return pre_dispatch_handoff(value, create_task, action="issue-backed task creation", available_routes=available_routes, policy_path=policy_path)


def send_cross_task_handoff(value: Any, send: Callable[[dict[str, Any]], Any], *, available_routes: dict[str, Any] | None = None, policy_path: Path | None = None) -> Any:
    """Canonical pre-dispatch path for a required peer handoff/wake-up."""

    return pre_dispatch_handoff(value, send, action="cross-task send", available_routes=available_routes, policy_path=policy_path)
