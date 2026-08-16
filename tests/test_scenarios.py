from __future__ import annotations

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
    Observation,
    OperationLedger,
    Reconciliation,
    TargetOperation,
    validate_handoff,
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


class HandoffAndCoordinationTests(unittest.TestCase):
    def handoff(self) -> dict:
        return json.loads(
            (ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/.agentic-sdlc/handoff-template.json").read_text(
                encoding="utf-8"
            )
        )

    def test_versioned_handoff_template_is_valid(self) -> None:
        self.assertEqual(validate_handoff(self.handoff()), [])

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
