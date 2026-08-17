from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from coordination_protocol import (  # noqa: E402
    DeliveryState,
    CoordinatorLifecycle,
    LifecycleState,
    LifecycleTransitionError,
    Observation,
    OperationLedger,
    ROLE_CODES,
    Reconciliation,
    SESSION_TITLE_MAX_CHARACTERS,
    TargetOperation,
    format_session_title,
    session_title_for_role,
    validate_handoff,
    validate_routing,
    validate_session_title_config,
)
import manage_repository  # noqa: E402


EXPECTED_ROLES = {
    "coordinator",
    "product",
    "architecture",
    "implementation",
    "qa",
    "reviewer",
    "knowledge_steward",
}


class RoleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.roles = ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/.codex/agents"

    def test_current_codex_agent_configuration_and_contract_completeness(self) -> None:
        found = set()
        required_sections = (
            "Inputs:",
            "Outputs:",
            "Terminal states:",
            "Escalation conditions:",
            "Forbidden actions:",
            "Permission posture:",
            "Authority:",
        )
        for path in self.roles.glob("*.toml"):
            value = tomllib.loads(path.read_text(encoding="utf-8"))
            found.add(value["name"])
            self.assertTrue(value["description"])
            self.assertIn(value["sandbox_mode"], {"read-only", "workspace-write"})
            for section in required_sections:
                self.assertIn(section, value["developer_instructions"], f"{path}: {section}")
        self.assertEqual(found, EXPECTED_ROLES)
        self.assertFalse((self.roles.parent / "config.toml").exists())

    def test_separation_of_duties_and_read_only_research_boundary(self) -> None:
        implementation = tomllib.loads((self.roles / "implementation.toml").read_text(encoding="utf-8"))
        self.assertIn("approving or merging own work", implementation["developer_instructions"])
        self.assertIn("never as an ephemeral subagent", implementation["developer_instructions"])
        for role in ("qa", "reviewer"):
            value = tomllib.loads((self.roles / f"{role}.toml").read_text(encoding="utf-8"))
            self.assertEqual(value["sandbox_mode"], "read-only")
            self.assertIn("independent", value["description"].lower())

    def test_adr_selection_stays_centralized(self) -> None:
        skill = (ROOT / "skills/adr-context/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Read only `docs/decisions/INDEX.md`", skill)
        for path in self.roles.glob("*.toml"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("adr-context", text)
            self.assertNotIn("Read only `docs/decisions/INDEX.md`", text)


class SessionTitleTests(unittest.TestCase):
    def test_all_seven_role_codes_are_stable_and_complete(self) -> None:
        self.assertEqual(
            ROLE_CODES,
            {
                "coordinator": "CO",
                "product": "PD",
                "architecture": "AR",
                "implementation": "IM",
                "qa": "QA",
                "reviewer": "RV",
                "knowledge_steward": "KS",
            },
        )
        for role, code in ROLE_CODES.items():
            with self.subTest(role=role):
                self.assertEqual(session_title_for_role(5, role, "Short title"), f"#5 {code} - Short title")

    def test_short_title_is_not_padded_or_truncated(self) -> None:
        self.assertEqual(format_session_title(5, "IM", "Fix bug"), "#5 IM - Fix bug")

    def test_exact_boundary_is_preserved_without_ellipsis(self) -> None:
        issue_title = "x" * 28
        title = format_session_title(5, "RV", issue_title)
        self.assertEqual(len(title), SESSION_TITLE_MAX_CHARACTERS)
        self.assertEqual(title, f"#5 RV - {issue_title}")
        self.assertFalse(title.endswith("…"))

    def test_overlong_title_truncates_only_issue_title(self) -> None:
        title = format_session_title(5, "IM", "x" * 29)
        self.assertEqual(title, f"#5 IM - {'x' * 27}…")
        self.assertEqual(len(title), SESSION_TITLE_MAX_CHARACTERS)

    def test_unicode_title_uses_character_length_not_encoded_bytes(self) -> None:
        title = format_session_title(123, "PD", "תכנון🚀" * 20)
        self.assertLessEqual(len(title), SESSION_TITLE_MAX_CHARACTERS)
        self.assertTrue(title.startswith("#123 PD - "))
        self.assertTrue(title.endswith("…"))
        self.assertNotIn("�", title.encode("utf-8").decode("utf-8"))

    def test_truncation_ends_with_one_unicode_ellipsis(self) -> None:
        title = format_session_title(5, "AR", ("a" * 27) + ("…" * 10))
        self.assertTrue(title.endswith("…"))
        self.assertFalse(title.endswith("……"))
        self.assertLessEqual(len(title), SESSION_TITLE_MAX_CHARACTERS)

    def test_unknown_role_code_and_role_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown role code"):
            format_session_title(5, "XX", "Issue title")
        with self.assertRaisesRegex(ValueError, "unknown role"):
            session_title_for_role(5, "scribe", "Issue title")

    def test_managed_config_matches_executable_title_authority(self) -> None:
        config_path = ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/.agentic-sdlc/config.yaml"
        config = config_path.read_text(encoding="utf-8")
        self.assertEqual(validate_session_title_config(config), [])
        invalid = config.replace("  reviewer: RV", "  reviewer: XX")
        self.assertTrue(validate_session_title_config(invalid))


class HandoffAndCoordinationTests(unittest.TestCase):
    def handoff(self) -> dict:
        return json.loads(
            (ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/.agentic-sdlc/handoff-template.json").read_text(
                encoding="utf-8"
            )
        )

    def test_versioned_handoff_template_is_valid(self) -> None:
        self.assertEqual(validate_handoff(self.handoff()), [])

    def test_routing_matrix_accepts_each_authorized_default(self) -> None:
        defaults = {
            "coordinator": ("gpt-5.6-sol", "Medium", "read-only"),
            "product": ("gpt-5.6-sol", "Medium", "read-only"),
            "architecture": ("gpt-5.6-sol", "Medium", "read-only"),
            "implementation": ("gpt-5.6-luna", "Low", "workspace-write"),
            "qa": ("gpt-5.6-sol", "Medium", "read-only"),
            "reviewer": ("gpt-5.6-sol", "Medium", "read-only"),
            "knowledge_steward": ("gpt-5.6-luna", "Low", "workspace-write"),
        }
        cases = 0
        for role, (model, effort, sandbox) in defaults.items():
            with self.subTest(role=role):
                value = self.handoff()
                value["to_role"] = role
                value["target_model"] = model
                value["effort"] = effort
                value["sandbox_mode"] = sandbox
                self.assertEqual(validate_handoff(value), [])
                cases += 1
        self.assertEqual(cases, 7)

    def test_cycle4_direction_identity_and_typed_intent_guards(self) -> None:
        lifecycle = CoordinatorLifecycle(5)
        evidence = {"commit_sha": "e" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence, source_task_key="issue-5-implementation", target_task_key="issue-5-reviewer")
        op = str(uuid4())
        lifecycle.observe_delivery_unknown("coordinator", op, LifecycleState.IMPLEMENTATION_READY, evidence, source_task_key="issue-5-implementation", target_task_key="issue-5-coordinator")
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", op, True), LifecycleState.IMPLEMENTATION_READY)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, op, evidence, source_task_key="issue-5-implementation", target_task_key="issue-5-coordinator")
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.REVIEW_ACTIVE, op, {**evidence, "commit_sha": "f" * 40}, source_task_key="issue-5-coordinator", target_task_key="issue-5-reviewer")

    def test_cycle5_handoff_tuple_binding_and_blocked_fallback(self) -> None:
        ready = self.handoff()
        ready.update({"lifecycle_state": "REVIEW_ACTIVE", "from_role": "implementation", "to_role": "reviewer", "source_task_key": "issue-1-implementation", "target_task_key": "issue-1-reviewer", "target_model": "gpt-5.6-sol", "effort": "Medium", "sandbox_mode": "read-only", "work_item": {**ready["work_item"], "pull_request_url": "https://github.com/o/r/pull/6", "commit_sha": "a" * 40}, "readiness_evidence": {"commit_sha": "b" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}})
        self.assertTrue(validate_handoff(ready))
        blocked = self.handoff()
        blocked["lifecycle_state"] = "BLOCKED"
        self.assertTrue(validate_handoff(blocked))
        blocked["blocked_fallback"] = {"repository": "o/r", "issue_or_pr_url": "https://github.com/o/r/issues/1", "operation_id": blocked["operation_id"], "objective": "Recover", "expected_output": "Handoff", "evidence": "PR evidence", "next_owner": "coordinator"}
        self.assertEqual(validate_handoff(blocked), [])

    def test_cycle7_absent_retry_and_canonical_repository_binding(self) -> None:
        lifecycle = CoordinatorLifecycle(5)
        operation_id = str(uuid4())
        lifecycle.observe_delivery_unknown("coordinator", operation_id, LifecycleState.IMPLEMENTATION_READY, None, source_task_key="issue-5-implementation", target_task_key="issue-5-coordinator")
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", operation_id, False), LifecycleState.IMPLEMENTATION_ACTIVE)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, operation_id, None, source_task_key="issue-5-implementation", target_task_key="issue-5-coordinator")

    def test_routing_requires_explicit_model_effort_and_one_sentence_rationale(self) -> None:
        for field in ("target_model", "effort", "rationale"):
            with self.subTest(field=field):
                value = self.handoff()
                value.pop(field)
                self.assertTrue(validate_handoff(value))
        value = self.handoff()
        value["rationale"] = "Two sentences. Not allowed."
        self.assertTrue(validate_handoff(value))

    def test_invalid_model_effort_and_role_pairings_fail(self) -> None:
        mutations = {
            "unknown model": {"target_model": "gpt-9.0", "effort": "Low"},
            "Sol implementation": {"to_role": "implementation", "target_model": "gpt-5.6-sol", "effort": "Medium", "sandbox_mode": "workspace-write"},
            "Luna reviewer": {"to_role": "reviewer", "target_model": "gpt-5.6-luna", "effort": "Low", "sandbox_mode": "read-only"},
            "Terra high without rationale": {"to_role": "implementation", "target_model": "gpt-5.6-terra", "effort": "High", "sandbox_mode": "workspace-write", "rationale": "A normal implementation task."},
            "Reviewer high without risk": {"to_role": "reviewer", "target_model": "gpt-5.6-sol", "effort": "High", "sandbox_mode": "read-only", "rationale": "A normal review task."},
        }
        for name, changes in mutations.items():
            with self.subTest(name=name):
                value = self.handoff()
                value.update(changes)
                self.assertTrue(validate_handoff(value))

    def test_correction_and_reviewer_routing_are_explicit(self) -> None:
        correction = self.handoff()
        correction.update({"is_correction": True, "target_model": "gpt-5.6-luna", "effort": "Low"})
        self.assertEqual(validate_handoff(correction), [])
        invalid = copy.deepcopy(correction)
        invalid["target_model"] = "gpt-5.6-sol"
        invalid["effort"] = "Medium"
        self.assertTrue(validate_handoff(invalid))
        reviewer = self.handoff()
        reviewer.update({"to_role": "reviewer", "target_model": "gpt-5.6-sol", "effort": "Medium", "sandbox_mode": "read-only"})
        self.assertEqual(validate_handoff(reviewer), [])

    def test_ephemeral_research_is_read_only_and_model_bounded(self) -> None:
        valid = self.handoff()
        valid.update({"execution_mode": "ephemeral_research", "sandbox_mode": "read-only", "target_model": "gpt-5.6-luna", "effort": "Low"})
        self.assertEqual(validate_handoff(valid), [])
        for name, changes in {
            "write-capable": {"sandbox_mode": "workspace-write"},
            "Terra medium": {"target_model": "gpt-5.6-terra", "effort": "Medium"},
            "Sol without exceptional rationale": {"target_model": "gpt-5.6-sol", "effort": "Medium"},
        }.items():
            with self.subTest(name=name):
                value = copy.deepcopy(valid)
                value.update(changes)
                self.assertTrue(validate_handoff(value))

    def test_lifecycle_requires_ready_evidence_and_coordinator_ownership(self) -> None:
        lifecycle = CoordinatorLifecycle()
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("implementation", LifecycleState.IMPLEMENTATION_READY, str(uuid4()))
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, str(uuid4()))
        evidence = {
            "commit_sha": "a" * 40,
            "pull_request_url": "https://github.com/o/r/pull/6",
            "local_gates": "passed",
            "ci_status": "passed",
            "required_checks": [{"name": "validate", "status": "passed"}],
        }
        self.assertEqual(
            lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence),
            LifecycleState.IMPLEMENTATION_READY,
        )

    def test_lifecycle_sequences_review_correction_and_human_gate(self) -> None:
        lifecycle = CoordinatorLifecycle()
        evidence = {"commit_sha": "a" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        for next_state in (LifecycleState.IMPLEMENTATION_READY, LifecycleState.REVIEW_ACTIVE):
            self.assertEqual(lifecycle.transition("coordinator", next_state, str(uuid4()), evidence), next_state)
        self.assertEqual(lifecycle.transition("coordinator", LifecycleState.CHANGES_REQUESTED, str(uuid4())), LifecycleState.CHANGES_REQUESTED)
        self.assertEqual(lifecycle.transition("coordinator", LifecycleState.CORRECTION_ACTIVE, str(uuid4())), LifecycleState.CORRECTION_ACTIVE)
        self.assertEqual(lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence), LifecycleState.IMPLEMENTATION_READY)
        self.assertEqual(lifecycle.transition("coordinator", LifecycleState.REVIEW_ACTIVE, str(uuid4()), evidence), LifecycleState.REVIEW_ACTIVE)
        self.assertEqual(lifecycle.transition("coordinator", LifecycleState.REVIEW_ACCEPTED, str(uuid4())), LifecycleState.REVIEW_ACCEPTED)
        self.assertEqual(lifecycle.transition("coordinator", LifecycleState.HUMAN_MERGE_READY, str(uuid4())), LifecycleState.HUMAN_MERGE_READY)

    def test_lifecycle_duplicate_operations_and_unknown_delivery_cannot_blind_activate(self) -> None:
        lifecycle = CoordinatorLifecycle()
        evidence = {"commit_sha": "b" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence)
        operation_id = str(uuid4())
        self.assertEqual(lifecycle.transition("coordinator", LifecycleState.REVIEW_ACTIVE, operation_id, evidence), LifecycleState.REVIEW_ACTIVE)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.REVIEW_ACTIVE, operation_id, evidence)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.REVIEW_ACCEPTED, operation_id)

        lifecycle = CoordinatorLifecycle()
        lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence)
        unknown_id = str(uuid4())
        self.assertEqual(lifecycle.observe_delivery_unknown("coordinator", unknown_id, LifecycleState.REVIEW_ACTIVE), LifecycleState.DELIVERY_UNKNOWN)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.REVIEW_ACTIVE, str(uuid4()), evidence)
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", unknown_id, False), LifecycleState.IMPLEMENTATION_READY)
        self.assertEqual(lifecycle.transition("coordinator", LifecycleState.REVIEW_ACTIVE, str(uuid4()), evidence), LifecycleState.REVIEW_ACTIVE)

    def test_lifecycle_can_block_when_host_task_control_is_unavailable(self) -> None:
        lifecycle = CoordinatorLifecycle()
        operation_id = str(uuid4())
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.BLOCKED, operation_id)
        fallback = {"repository": "o/r", "issue_or_pr_url": "https://github.com/o/r/issues/1", "operation_id": operation_id, "objective": "Recover", "expected_output": "Handoff", "evidence": "PR evidence", "next_owner": "coordinator"}
        self.assertEqual(lifecycle.transition("coordinator", LifecycleState.BLOCKED, operation_id, fallback=fallback), LifecycleState.BLOCKED)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, str(uuid4()), {})

    def test_lifecycle_handoff_validation_requires_exact_ready_and_review_evidence(self) -> None:
        ready = self.handoff()
        ready.update(
            {
                "lifecycle_state": "IMPLEMENTATION_READY",
                "from_role": "implementation",
                "to_role": "coordinator",
                "source_task_key": "issue-1-implementation",
                "target_task_key": "issue-1-coordinator",
                "target_model": "gpt-5.6-sol",
                "effort": "Medium",
                "sandbox_mode": "read-only",
                "evidence": ["local gates passed", "CI passed"],
                "readiness_evidence": {"commit_sha": "c" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]},
                "work_item": {
                    **ready["work_item"],
                    "repository": "o/r",
                    "pull_request_url": "https://github.com/o/r/pull/6",
                    "commit_sha": "c" * 40,
                },
            }
        )
        self.assertEqual(validate_handoff(ready), [])
        ready["work_item"]["commit_sha"] = "not-exact"
        self.assertTrue(validate_handoff(ready))

        reviewer = self.handoff()
        reviewer.update(
            {
                "lifecycle_state": "REVIEW_ACTIVE",
                "lifecycle_event": "REVIEW_ACTIVATE",
                "to_role": "reviewer",
                "from_role": "coordinator",
                "source_task_key": "issue-1-coordinator",
                "target_task_key": "issue-1-reviewer",
                "target_model": "gpt-5.6-sol",
                "effort": "Medium",
                "sandbox_mode": "read-only",
                "work_item": {
                    **reviewer["work_item"],
                    "repository": "o/r",
                    "pull_request_url": "https://github.com/o/r/pull/6",
                    "commit_sha": "d" * 40,
                },
                "readiness_evidence": {"commit_sha": "d" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]},
            }
        )
        self.assertEqual(validate_handoff(reviewer), [])
        reviewer["target_model"] = "gpt-5.6-luna"
        self.assertTrue(validate_handoff(reviewer))

    def test_executable_handoff_validation_enforces_every_schema_constraint(self) -> None:
        mutations = {
            "root type": lambda value: [],
            "missing required": lambda value: value.pop("objective"),
            "empty required string": lambda value: value.__setitem__("objective", ""),
            "watchdog below minimum": lambda value: value.__setitem__("watchdog_seconds", 0),
            "watchdog above maximum": lambda value: value.__setitem__("watchdog_seconds", 31),
            "watchdog boolean is not integer": lambda value: value.__setitem__("watchdog_seconds", True),
            "unexpected extra property": lambda value: value.__setitem__("unexpected", True),
            "duplicate unique-list entries": lambda value: value["inputs"].append(value["inputs"][0]),
            "empty list item": lambda value: value["constraints"].append(""),
            "list type": lambda value: value.__setitem__("outputs", "not-a-list"),
            "schema const": lambda value: value.__setitem__("schema_version", "2.0.0"),
            "operation pattern": lambda value: value.__setitem__("operation_id", "00000000-0000-7000-8000-000000000000"),
            "role enum": lambda value: value.__setitem__("to_role", "scribe"),
            "terminal enum": lambda value: value.__setitem__("terminal_state", "DELIVERY_UNKNOWN"),
            "work item extra": lambda value: value["work_item"].__setitem__("thread_id", "local-only"),
            "repository pattern": lambda value: value["work_item"].__setitem__("repository", "owner/repo/extra"),
            "issue minimum": lambda value: value["work_item"].__setitem__("issue_number", 0),
            "issue boolean is not integer": lambda value: value["work_item"].__setitem__("issue_number", True),
            "issue URI": lambda value: value["work_item"].__setitem__("issue_url", "not-a-uri"),
            "empty work item title": lambda value: value["work_item"].__setitem__("title", ""),
            "pull request URI": lambda value: value["work_item"].__setitem__("pull_request_url", "relative/path"),
            "commit SHA": lambda value: value["work_item"].__setitem__("commit_sha", "abc123"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                value = self.handoff()
                mutated = mutate(value)
                candidate = mutated if name == "root type" else value
                self.assertTrue(validate_handoff(candidate), f"mutation accepted: {name}")

    def operation(self, target: str = "task-a") -> TargetOperation:
        return TargetOperation(str(uuid4()), target, "https://github.com/o/r/issues/1", "Apply finding F-1")

    def test_success_and_explicit_non_delivery(self) -> None:
        delivered = self.operation()
        self.assertEqual(
            delivered.observe(Observation.DELIVERED_AND_ACKNOWLEDGED, authoritative_confirmed=True),
            DeliveryState.DELIVERED,
        )
        absent = self.operation()
        self.assertEqual(absent.observe(Observation.EXPLICIT_NON_DELIVERY), DeliveryState.NOT_DELIVERED)
        self.assertTrue(absent.retry_allowed)

    def test_ack_failure_timeout_and_handler_failure_are_unknown(self) -> None:
        for observation in (
            Observation.DELIVERED_BUT_ACK_FAILED,
            Observation.TIMEOUT,
            Observation.HANDLER_FAILURE,
        ):
            operation = self.operation()
            self.assertEqual(operation.observe(observation), DeliveryState.DELIVERY_UNKNOWN)
            self.assertFalse(operation.retry_allowed)

    def test_unknown_requires_recorded_absent_reconciliation_before_retry(self) -> None:
        operation = self.operation()
        operation.observe(Observation.TIMEOUT)
        self.assertEqual(operation.observe(Observation.EXPLICIT_NON_DELIVERY), DeliveryState.DELIVERY_UNKNOWN)
        self.assertFalse(operation.retry_allowed)
        self.assertEqual(operation.reconciliation_count, 0)
        self.assertIsNone(operation.last_reconciliation)

        self.assertEqual(operation.reconcile(Reconciliation.UNAVAILABLE), DeliveryState.DELIVERY_UNKNOWN)
        self.assertFalse(operation.retry_allowed)
        self.assertEqual(operation.reconciliation_count, 1)
        self.assertEqual(operation.last_reconciliation, Reconciliation.UNAVAILABLE)

        self.assertEqual(operation.reconcile(Reconciliation.ABSENT), DeliveryState.NOT_DELIVERED)
        self.assertTrue(operation.retry_allowed)
        self.assertEqual(operation.reconciliation_count, 2)
        self.assertEqual(operation.last_reconciliation, Reconciliation.ABSENT)

    def test_delivered_operation_is_terminal_and_cannot_be_reopened(self) -> None:
        delivered = self.operation()
        delivered.observe(Observation.DELIVERED_AND_ACKNOWLEDGED, authoritative_confirmed=True)
        applied = self.operation()
        applied.observe(Observation.TIMEOUT)
        applied.reconcile(Reconciliation.APPLIED)
        for operation in (delivered, applied):
            with self.subTest(origin=operation.last_reconciliation or "acknowledgement"):
                attempts = operation.attempt_count
                reconciliations = operation.reconciliation_count
                for transition in (
                    lambda: operation.observe(Observation.TIMEOUT),
                    lambda: operation.observe(Observation.EXPLICIT_NON_DELIVERY),
                    lambda: operation.reconcile(Reconciliation.ABSENT),
                    lambda: operation.reconcile(Reconciliation.UNAVAILABLE),
                ):
                    self.assertEqual(transition(), DeliveryState.DELIVERED)
                    self.assertTrue(operation.side_effect_confirmed)
                    self.assertFalse(operation.retry_allowed)
                    self.assertEqual(operation.attempt_count, attempts)
                    self.assertEqual(operation.reconciliation_count, reconciliations)

    def test_reconciliation_prevents_duplicates_and_stops_when_unavailable(self) -> None:
        operation = self.operation()
        operation.observe(Observation.TIMEOUT)
        self.assertEqual(operation.reconcile(Reconciliation.APPLIED), DeliveryState.DELIVERED)
        self.assertFalse(operation.retry_allowed)
        unavailable = self.operation()
        unavailable.observe(Observation.TIMEOUT)
        self.assertEqual(unavailable.reconcile(Reconciliation.UNAVAILABLE), DeliveryState.DELIVERY_UNKNOWN)
        self.assertFalse(unavailable.retry_allowed)

    def test_operation_ids_are_idempotent_and_per_target_state_isolated(self) -> None:
        ledger = OperationLedger()
        first = ledger.register(self.operation("task-a"))
        self.assertIs(first, ledger.register(TargetOperation(first.operation_id, "task-a", first.authoritative_ref, first.objective)))
        with self.assertRaises(ValueError):
            ledger.register(TargetOperation(first.operation_id, "task-b", first.authoritative_ref, "Different action"))
        second = ledger.register(self.operation("task-b"))
        first.observe(Observation.EXPLICIT_NON_DELIVERY)
        second.observe(Observation.DELIVERED_AND_ACKNOWLEDGED, authoritative_confirmed=True)
        self.assertEqual(first.state, DeliveryState.NOT_DELIVERED)
        self.assertEqual(second.state, DeliveryState.DELIVERED)
        duplicate = ledger.register(TargetOperation(second.operation_id, "task-b", second.authoritative_ref, second.objective))
        duplicate.observe(Observation.EXPLICIT_NON_DELIVERY)
        self.assertEqual(duplicate.state, DeliveryState.DELIVERED)
        self.assertEqual(second.attempt_count, 1)

    def test_github_recovery_fallback_contains_no_thread_id(self) -> None:
        fallback = self.operation().fallback()
        self.assertIn("authoritative_ref", fallback)
        self.assertIn("operation_id", fallback)
        self.assertIn("expected_output", fallback)
        self.assertIn("evidence", fallback)
        self.assertIn("next_owner", fallback)
        self.assertNotIn("thread_id", fallback)

    def test_durable_routing_and_watchdog_policy(self) -> None:
        coordination = (
            ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/docs/agentic-sdlc/coordination.md"
        ).read_text(encoding="utf-8")
        config = (
            ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/.agentic-sdlc/config.yaml"
        ).read_text(encoding="utf-8")
        for risk_class in ("file-producing", "durable-artifact-producing", "decision-heavy", "release", "high-importance", "risk-bearing"):
            self.assertIn(risk_class, coordination)
        self.assertIn("bounded read-only", coordination)
        self.assertIn("cross_task_watchdog_seconds: 25", config)
        self.assertIn("routing_policy: repository-native-v1", config)
        self.assertIn("gpt-5.6-luna/Low|gpt-5.6-terra/Low..High", config)


class RepositoryStateTests(unittest.TestCase):
    def copy_fixture(self, name: str) -> Path:
        temporary = ROOT / "tests" / ".tmp" / str(uuid4())
        temporary.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, temporary)
        target = temporary / name
        shutil.copytree(ROOT / "tests/fixtures" / name, target)
        return target

    def apply_fixture(self, name: str, project_name: str) -> tuple[Path, list[manage_repository.Action]]:
        target = self.copy_fixture(name)
        actions, writes, obsolete = manage_repository.plan(target, project_name)
        self.assertFalse([action for action in actions if action.classification == "conflict"])
        manage_repository.apply(target, writes, obsolete, project_name)
        return target, actions

    def test_v0_2_upgrade_removes_generated_registry_and_is_idempotent(self) -> None:
        target, actions = self.apply_fixture("v0_2_repository", "Legacy Fixture")
        classifications = {(action.classification, action.path.as_posix()) for action in actions}
        self.assertIn(("delete", ".codex/config.toml"), classifications)
        self.assertFalse((target / ".codex/config.toml").exists())
        self.assertEqual(tomllib.loads((target / ".codex/agents/coordinator.toml").read_text(encoding="utf-8"))["name"], "coordinator")
        config = (target / ".agentic-sdlc/config.yaml").read_text(encoding="utf-8")
        self.assertIn('project_name: "Legacy Fixture"', config)
        self.assertIn('session_title_format: "#{issue_number} {role_code} - {issue_title}"', config)
        self.assertIn("routing_policy: repository-native-v1", config)
        self.assertNotIn("{project_name} #{issue_number}", config)
        again, _, _ = manage_repository.plan(target, "Legacy Fixture")
        self.assertFalse([a for a in again if a.classification in {"create", "update", "delete", "managed-block-update", "conflict"}])

    def test_custom_content_and_project_codex_config_are_preserved(self) -> None:
        target = self.copy_fixture("customized_repository")
        agents_before = (target / "AGENTS.md").read_text(encoding="utf-8")
        config_before = (target / ".codex/config.toml").read_text(encoding="utf-8")
        decisions_before = (target / "docs/decisions/INDEX.md").read_text(encoding="utf-8")
        actions, writes, obsolete = manage_repository.plan(target, "Customized Fixture")
        self.assertFalse([action for action in actions if action.classification == "conflict"])
        manage_repository.apply(target, writes, obsolete, "Customized Fixture")
        self.assertTrue((target / "AGENTS.md").read_text(encoding="utf-8").startswith(agents_before))
        self.assertEqual((target / ".codex/config.toml").read_text(encoding="utf-8"), config_before)
        self.assertEqual((target / "docs/decisions/INDEX.md").read_text(encoding="utf-8"), decisions_before)

    def test_managed_drift_is_a_conflict(self) -> None:
        target, _ = self.apply_fixture("customized_repository", "Customized Fixture")
        role = target / ".codex/agents/product.toml"
        role.write_text(role.read_text(encoding="utf-8") + "\n# local edit\n", encoding="utf-8")
        actions, _, _ = manage_repository.plan(target, "Customized Fixture")
        self.assertIn(("conflict", ".codex/agents/product.toml"), {(a.classification, a.path.as_posix()) for a in actions})

    def assert_check_conflict(self, target: Path, reason_fragment: str) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/manage_repository.py"), "check", "--target", str(target)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("conflict: .agentic-sdlc/managed.json", result.stdout)
        self.assertIn(reason_fragment, result.stdout)

    def test_manifest_plugin_version_drift_is_a_conflict(self) -> None:
        target, _ = self.apply_fixture("customized_repository", "Customized Fixture")
        manifest_path = target / ".agentic-sdlc/managed.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["plugin_version"] = "99.0.0"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assert_check_conflict(target, "plugin_version")

    def test_manifest_false_managed_file_hash_is_a_conflict(self) -> None:
        target, _ = self.apply_fixture("customized_repository", "Customized Fixture")
        manifest_path = target / ".agentic-sdlc/managed.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][".codex/agents/product.toml"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assert_check_conflict(target, "hash does not match .codex/agents/product.toml")

    def test_missing_manifest_is_reported_as_drift(self) -> None:
        target, _ = self.apply_fixture("customized_repository", "Customized Fixture")
        (target / ".agentic-sdlc/managed.json").unlink()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/manage_repository.py"), "check", "--target", str(target)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("create: .agentic-sdlc/managed.json", result.stdout)


if __name__ == "__main__":
    unittest.main()
