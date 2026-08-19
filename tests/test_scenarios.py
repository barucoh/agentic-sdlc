from __future__ import annotations

import copy
import json
import re
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
    lifecycle_routes,
    lifecycle_tuple,
    session_title_for_role,
    validate_handoff,
    validate_routing,
    validate_session_title_config,
    dispatch_issue_task,
    send_cross_task_handoff,
)
from qa_workspace import WorkspaceSnapshot, capture_snapshot, verify_qa_workspace  # noqa: E402
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
            expected_mode = "workspace-write" if role == "qa" else "read-only"
            self.assertEqual(value["sandbox_mode"], expected_mode)
            self.assertIn("independent", value["description"].lower())
            if role == "qa":
                contract = value["developer_instructions"].lower()
                for phrase in ("disposable", "isolated", "no source", "no commit", "no push", "cleanup"):
                    self.assertIn(phrase, contract)

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
            session_title_for_role(5, "unknown_role", "Issue title")

    def test_managed_config_matches_executable_title_authority(self) -> None:
        config_path = ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/.agentic-sdlc/config.yaml"
        config = config_path.read_text(encoding="utf-8")
        self.assertEqual(validate_session_title_config(config), [])
        invalid = config.replace("  reviewer: RV", "  reviewer: XX")
        self.assertTrue(validate_session_title_config(invalid))


class HandoffAndCoordinationTests(unittest.TestCase):
    def lifecycle(self) -> CoordinatorLifecycle:
        return CoordinatorLifecycle({"repository": "o/r", "issue_number": 5, "issue_url": "https://github.com/o/r/issues/5"})

    def peer_transition(self, lifecycle: CoordinatorLifecycle, next_state: LifecycleState, operation_id: str, evidence: dict | None = None, **kwargs: object) -> LifecycleState:
        """Drive the protocol as the peer that owns the authoritative source key."""
        if next_state in {LifecycleState.IMPLEMENTATION_READY, LifecycleState.QA_PASSED, LifecycleState.REVIEW_ACCEPTED, LifecycleState.HUMAN_MERGE_READY}:
            if evidence is None:
                evidence = {"commit_sha": lifecycle._artifact[1], "pull_request_url": lifecycle._artifact[0], "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
            role_evidence = lambda role: {"role": role, "commit_sha": evidence["commit_sha"], "pull_request_url": evidence["pull_request_url"], "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
            evidence = {**evidence, "implementation_evidence": role_evidence("implementation"), "qa_evidence": role_evidence("qa"), "review_evidence": role_evidence("reviewer")}
        event = kwargs.get("event") or lifecycle._event_for(next_state)
        source = kwargs.get("source_task_key")
        target = kwargs.get("target_task_key")
        if source is None and target is None:
            route = lifecycle_tuple(lifecycle.issue_number, lifecycle.state, next_state, event)
            self.assertIsNotNone(route)
            source, target = route[2:]
            kwargs["source_task_key"] = source
            kwargs["target_task_key"] = target
        route = next(item for item in lifecycle_routes(lifecycle.issue_number, lifecycle.state, next_state, event) if item[2] == source and target in item[3])
        kwargs.setdefault("recipient_operation_ids", {key: str(uuid4()) for key in route[3]})
        self.assertIsInstance(source, str)
        actor = source.rsplit("-", 1)[1]
        return lifecycle.transition(actor, next_state, operation_id, evidence, **kwargs)

    def peer_unknown(self, lifecycle: CoordinatorLifecycle, operation_id: str, intended_state: LifecycleState, evidence: dict | None = None, **kwargs: object) -> LifecycleState:
        """Apply the parent event once, then mark its first exact child uncertain."""
        event = kwargs.get("event") or lifecycle._event_for(intended_state)
        source = kwargs.get("source_task_key")
        target = kwargs.get("target_task_key")
        if source is None and target is None:
            route = lifecycle_tuple(lifecycle.issue_number, lifecycle.state, intended_state, event)
            self.assertIsNotNone(route)
            source, target = route[2:]
            kwargs["source_task_key"] = source
            kwargs["target_task_key"] = target
        route = next(item for item in lifecycle_routes(lifecycle.issue_number, lifecycle.state, intended_state, event) if item[2] == source and target in item[3])
        recipients = kwargs.setdefault("recipient_operation_ids", {key: str(uuid4()) for key in route[3]})
        self.assertIsInstance(source, str)
        actor = source.rsplit("-", 1)[1]
        result = self.peer_transition(lifecycle, intended_state, operation_id, evidence, **kwargs)
        self.assertIsInstance(recipients, dict)
        lifecycle.observe_delivery_unknown(actor, recipients[route[3][0]], intended_state, target_task_key=route[3][0])
        return result

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
            "qa": ("gpt-5.6-sol", "Medium", "workspace-write"),
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
                self.assertEqual(validate_routing(value), [])
                cases += 1
        self.assertEqual(cases, 7)

    def test_pre_dispatch_validation_blocks_task_creation_and_send_before_side_effects(self) -> None:
        invalid = self.handoff()
        invalid["objective"] = ""
        calls: list[str] = []
        with self.assertRaisesRegex(LifecycleTransitionError, "issue-backed task creation blocked"):
            dispatch_issue_task(invalid, lambda _: calls.append("create"))
        with self.assertRaisesRegex(LifecycleTransitionError, "cross-task send blocked"):
            send_cross_task_handoff(invalid, lambda _: calls.append("send"))
        self.assertEqual(calls, [])

        valid = self.handoff()
        self.assertEqual(dispatch_issue_task(valid, lambda envelope: (calls.append("create"), envelope)[1]), valid)
        self.assertEqual(send_cross_task_handoff(valid, lambda envelope: (calls.append("send"), envelope)[1]), valid)
        self.assertEqual(calls, ["create", "send"])

    def test_handoff_validator_cli_uses_the_same_authority(self) -> None:
        valid = self.handoff()
        command = [sys.executable, str(ROOT / "scripts/validate_handoff.py")]
        accepted = subprocess.run(command, input=json.dumps(valid), text=True, capture_output=True, check=False)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("valid handoff", accepted.stdout)
        invalid = copy.deepcopy(valid)
        invalid["objective"] = ""
        rejected = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("objective", rejected.stdout)

    def test_native_task_hook_uses_canonical_validator_and_observed_tool_names(self) -> None:
        hook_config = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
        group = hook_config["hooks"]["PreToolUse"][0]
        self.assertIn("codex_app__create_thread", group["matcher"])
        self.assertIn("codex_app__send_message_to_thread", group["matcher"])
        self.assertNotIn("hooks", json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")))
        guard = ROOT / "hooks/pretool_handoff_guard.py"
        environment = {**__import__("os").environ, "PLUGIN_ROOT": str(ROOT)}
        valid_event = {"tool_name": "codex_app__create_thread", "tool_input": {"prompt": "ASDLC_HANDOFF: " + json.dumps(self.handoff())}}
        allowed = subprocess.run([sys.executable, str(guard)], input=json.dumps(valid_event), text=True, capture_output=True, env=environment, check=False)
        self.assertEqual(allowed.returncode, 0)
        self.assertEqual(allowed.stdout.strip(), "")
        invalid = self.handoff()
        invalid["objective"] = ""
        denied = subprocess.run([sys.executable, str(guard)], input=json.dumps({"tool_name": "codex_app__send_message_to_thread", "tool_input": {"metadata": {"handoff": invalid}}}), text=True, capture_output=True, env=environment, check=False)
        self.assertEqual(denied.returncode, 0)
        decision = json.loads(denied.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("ASDLC_HANDOFF_INVALID", decision["permissionDecisionReason"])
        self.assertIn("no task/message was created", decision["permissionDecisionReason"])
        self.assertEqual(subprocess.run([sys.executable, str(ROOT / "scripts/validate_hooks.py")], text=True, capture_output=True, check=False).returncode, 0)

    def test_qa_workspace_is_disposable_and_source_immutable(self) -> None:
        contract = tomllib.loads((ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/.codex/agents/qa.toml").read_text(encoding="utf-8"))["developer_instructions"].lower()
        self.assertIn("isolated disposable qa clone", contract)
        self.assertIn("behavioral tools may create caches/build/test outputs", contract)
        self.assertIn("source mutation", contract)

    def test_qa_workspace_check_binds_exact_head_and_rejects_source_intent(self) -> None:
        before = WorkspaceSnapshot(head="a" * 40, branch="", status=(), source_diff="", implementation_ref=("refs/heads/main", "a" * 40), git_common_dir="qa-common")
        after = WorkspaceSnapshot(head="a" * 40, branch="", status=(), source_diff="", implementation_ref=("refs/heads/main", "a" * 40), git_common_dir="qa-common")
        valid = verify_qa_workspace(
            before,
            after,
            expected_sha=after.head,
            implementation_branch="main",
            declared_outputs=[".pytest_cache", "build", "dist"],
            implementation_repository_common_dir="implementation-common",
        )
        self.assertTrue(valid.passed)
        self.assertIn("host/task-control", valid.cleanup_responsibility)
        invalid = verify_qa_workspace(
            before,
            after,
            expected_sha="0" * 40,
            implementation_branch="main",
            declared_outputs=[".pytest_cache"],
            intents=["commit"],
            implementation_repository_common_dir="implementation-common",
        )
        self.assertFalse(invalid.passed)
        self.assertTrue(any("exact expected" in error for error in invalid.errors))
        self.assertTrue(any("forbidden" in error for error in invalid.errors))

    def test_qa_workspace_rejects_preexisting_dirty_snapshot(self) -> None:
        clean = WorkspaceSnapshot(head="a" * 40, branch="", status=(), source_diff="", implementation_ref=("refs/heads/main", "a" * 40), git_common_dir="qa-common")
        dirty = WorkspaceSnapshot(head="a" * 40, branch="", status=(" M source.py",), source_diff="diff", implementation_ref=("refs/heads/main", "a" * 40), git_common_dir="qa-common")
        result = verify_qa_workspace(dirty, dirty, expected_sha="a" * 40, implementation_branch="main", exit_code=0, implementation_repository_common_dir="implementation-common")
        self.assertFalse(result.passed)
        self.assertIn("pre-existing", " ".join(result.errors))
        self.assertEqual(result.before.head, result.after.head)
        self.assertTrue(verify_qa_workspace(clean, clean, expected_sha="a" * 40, implementation_branch="main", implementation_repository_common_dir="implementation-common").passed)

    def test_cycle4_direction_identity_and_typed_intent_guards(self) -> None:
        lifecycle = self.lifecycle()
        evidence = {"commit_sha": "e" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("implementation", LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence, source_task_key="issue-5-implementation", target_task_key="issue-5-reviewer")
        op = str(uuid4())
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        self.peer_unknown(lifecycle, op, LifecycleState.IMPLEMENTATION_READY, evidence)
        child_id = lifecycle._operations[op].recipient_operation_ids[0]
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", child_id, True), LifecycleState.IMPLEMENTATION_READY)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("implementation", LifecycleState.IMPLEMENTATION_READY, op, evidence, source_task_key="issue-5-implementation", target_task_key="issue-5-qa")
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("qa", LifecycleState.QA_PASSED, op, {**evidence, "commit_sha": "f" * 40}, source_task_key="issue-5-qa", target_task_key="issue-5-reviewer")

    def test_cycle5_handoff_tuple_binding_and_blocked_fallback(self) -> None:
        ready = self.handoff()
        ready.update({"lifecycle_state": "REVIEW_ACTIVE", "from_role": "implementation", "to_role": "reviewer", "source_task_key": "issue-1-implementation", "target_task_key": "issue-1-reviewer", "target_model": "gpt-5.6-sol", "effort": "Medium", "sandbox_mode": "read-only", "work_item": {**ready["work_item"], "pull_request_url": "https://github.com/o/r/pull/6", "commit_sha": "a" * 40}, "readiness_evidence": {"commit_sha": "b" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}})
        self.assertTrue(validate_handoff(ready))
        blocked = self.handoff()
        blocked.update({"lifecycle_state": "BLOCKED", "lifecycle_event": "BLOCKED", "from_role": "coordinator", "to_role": "coordinator", "source_task_key": "issue-1-coordinator", "target_task_key": "issue-1-coordinator", "target_model": "gpt-5.6-sol", "effort": "Medium", "sandbox_mode": "read-only"})
        self.assertTrue(validate_handoff(blocked))
        blocked["blocked_fallback"] = {"repository": "OWNER/REPOSITORY", "issue_or_pr_url": "https://github.com/OWNER/REPOSITORY/issues/1", "operation_id": blocked["operation_id"], "objective": "Recover", "expected_output": "Handoff", "evidence": "PR evidence", "next_owner": "coordinator"}
        blocked["recipient_task_keys"] = ["issue-1-coordinator"]
        blocked["recipient_operation_ids"] = ["00000000-0000-4000-8000-000000000003"]
        self.assertEqual(validate_handoff(blocked), [])

    def test_cycle7_absent_retry_and_canonical_repository_binding(self) -> None:
        lifecycle = self.lifecycle()
        evidence = {"commit_sha": "a" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        operation_id = str(uuid4())
        self.peer_unknown(lifecycle, operation_id, LifecycleState.IMPLEMENTATION_READY, evidence)
        child_id = lifecycle._operations[operation_id].recipient_operation_ids[0]
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", child_id, False), LifecycleState.IMPLEMENTATION_READY)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("implementation", LifecycleState.IMPLEMENTATION_READY, operation_id, None, source_task_key="issue-5-implementation", target_task_key="issue-5-qa")

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
        correction.update({"is_correction": True, "to_role": "implementation", "target_model": "gpt-5.6-luna", "effort": "Low", "sandbox_mode": "workspace-write"})
        self.assertEqual(validate_routing(correction), [])
        invalid = copy.deepcopy(correction)
        invalid["target_model"] = "gpt-5.6-sol"
        invalid["effort"] = "Medium"
        self.assertTrue(validate_handoff(invalid))
        reviewer = self.handoff()
        reviewer.update({"to_role": "reviewer", "target_model": "gpt-5.6-sol", "effort": "Medium", "sandbox_mode": "read-only"})
        self.assertEqual(validate_routing(reviewer), [])

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
        lifecycle = self.lifecycle()
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.IMPLEMENTATION_READY, str(uuid4()))
        evidence = {
            "commit_sha": "a" * 40,
            "pull_request_url": "https://github.com/o/r/pull/6",
            "local_gates": "passed",
            "ci_status": "passed",
            "required_checks": [{"name": "validate", "status": "passed"}],
        }
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        self.assertEqual(
            self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence),
            LifecycleState.IMPLEMENTATION_READY,
        )

    def test_lifecycle_sequences_review_correction_and_human_gate(self) -> None:
        lifecycle = self.lifecycle()
        evidence = {"commit_sha": "a" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        for next_state in (LifecycleState.IMPLEMENTATION_READY, LifecycleState.QA_PASSED, LifecycleState.REVIEW_ACTIVE):
            self.assertEqual(self.peer_transition(lifecycle, next_state, str(uuid4()), evidence), next_state)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.CHANGES_REQUESTED, str(uuid4())), LifecycleState.CHANGES_REQUESTED)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.CORRECTION_ACTIVE, str(uuid4())), LifecycleState.CORRECTION_ACTIVE)
        evidence = {**evidence, "commit_sha": "b" * 40}
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence), LifecycleState.IMPLEMENTATION_READY)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.QA_PASSED, str(uuid4()), evidence), LifecycleState.QA_PASSED)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.REVIEW_ACTIVE, str(uuid4()), evidence), LifecycleState.REVIEW_ACTIVE)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.REVIEW_ACCEPTED, str(uuid4())), LifecycleState.REVIEW_ACCEPTED)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.HUMAN_MERGE_READY, str(uuid4())), LifecycleState.HUMAN_MERGE_READY)

    def test_delivery_cell_peer_correction_loop_and_reviewer_terminal_to_coordinator(self) -> None:
        lifecycle = self.lifecycle()
        first = {"commit_sha": "a" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.bind_delivery_artifact(str(uuid4()), first["pull_request_url"], first["commit_sha"])
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, str(uuid4()), first), LifecycleState.IMPLEMENTATION_READY)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("coordinator", LifecycleState.QA_CHANGES_REQUESTED, str(uuid4()))
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.QA_CHANGES_REQUESTED, str(uuid4())), LifecycleState.QA_CHANGES_REQUESTED)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.CORRECTION_ACTIVE, str(uuid4())), LifecycleState.CORRECTION_ACTIVE)
        corrected = {**first, "commit_sha": "b" * 40}
        lifecycle.bind_delivery_artifact(str(uuid4()), corrected["pull_request_url"], corrected["commit_sha"])
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, str(uuid4()), corrected), LifecycleState.IMPLEMENTATION_READY)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.QA_PASSED, str(uuid4()), corrected), LifecycleState.QA_PASSED)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.REVIEW_ACTIVE, str(uuid4()), corrected), LifecycleState.REVIEW_ACTIVE)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("reviewer", LifecycleState.HUMAN_MERGE_READY, str(uuid4()))
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.REVIEW_ACCEPTED, str(uuid4())), LifecycleState.REVIEW_ACCEPTED)
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.HUMAN_MERGE_READY, str(uuid4())), LifecycleState.HUMAN_MERGE_READY)

    def test_delivery_unknown_peer_timeout_recovers_without_duplicate_transition(self) -> None:
        lifecycle = self.lifecycle()
        evidence = {"commit_sha": "a" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        operation_id = str(uuid4())
        self.assertEqual(self.peer_unknown(lifecycle, operation_id, LifecycleState.IMPLEMENTATION_READY, evidence), LifecycleState.IMPLEMENTATION_READY)
        parent = lifecycle._operations[operation_id]
        child_id = parent.recipient_operation_ids[0]
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", child_id, True), LifecycleState.IMPLEMENTATION_READY)
        self.assertEqual(lifecycle._operations[child_id].state, DeliveryState.DELIVERED)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("implementation", LifecycleState.IMPLEMENTATION_READY, operation_id, evidence)

    def test_delivery_cell_fanout_and_terminal_evidence_are_immutable(self) -> None:
        lifecycle = self.lifecycle()
        evidence = {"commit_sha": "a" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        operation_id = str(uuid4())
        recipients = {"issue-5-qa": str(uuid4()), "issue-5-reviewer": str(uuid4())}
        self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, operation_id, evidence, source_task_key="issue-5-implementation", target_task_key="issue-5-qa", recipient_operation_ids=recipients)
        self.assertEqual(lifecycle._operations[operation_id].recipient_task_keys, ("issue-5-qa", "issue-5-reviewer"))
        self.assertEqual(lifecycle._operations[recipients["issue-5-qa"]].parent_operation_id, operation_id)
        lifecycle.observe_delivery_unknown("implementation", recipients["issue-5-reviewer"], LifecycleState.IMPLEMENTATION_READY, target_task_key="issue-5-reviewer")
        self.assertEqual(lifecycle._operations[recipients["issue-5-qa"]].state, DeliveryState.PENDING)
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", recipients["issue-5-reviewer"], False), LifecycleState.IMPLEMENTATION_READY)
        self.assertEqual(lifecycle._operations[recipients["issue-5-reviewer"]].state, DeliveryState.NOT_DELIVERED)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.observe_delivery_unknown("implementation", recipients["issue-5-reviewer"], target_task_key="issue-5-reviewer")
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.reconcile_delivery("coordinator", recipients["issue-5-reviewer"], Reconciliation.APPLIED)
        self.assertEqual(lifecycle.retry_delivery("implementation", recipients["issue-5-reviewer"]), DeliveryState.PENDING)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("qa", LifecycleState.QA_PASSED, str(uuid4()), evidence, source_task_key="issue-5-qa", target_task_key="issue-5-reviewer", recipient_operation_ids={"issue-5-reviewer": str(uuid4()), "issue-5-implementation": str(uuid4())})

    def test_child_delivery_identity_is_frozen_while_state_reconciles(self) -> None:
        lifecycle = self.lifecycle()
        evidence = {"commit_sha": "1" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        parent_id = str(uuid4())
        self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, parent_id, evidence)
        child_id = lifecycle._operations[parent_id].recipient_operation_ids[0]
        child = lifecycle._operations[child_id]
        for field, replacement in (("operation_id", str(uuid4())), ("parent_operation_id", str(uuid4())), ("recipient_task_key", "issue-5-coordinator"), ("recipient_role", "coordinator")):
            with self.subTest(field=field):
                with self.assertRaises((AttributeError, LifecycleTransitionError, TypeError)):
                    setattr(child, field, replacement)
        with self.assertRaises(LifecycleTransitionError):
            child.identity = child.identity
        self.assertEqual(child.operation_id, child_id)
        lifecycle.observe_delivery_unknown("implementation", child_id, target_task_key=child.recipient_task_key)
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", child_id, Reconciliation.APPLIED), LifecycleState.IMPLEMENTATION_READY)

    def test_terminal_handoff_uses_its_complete_structured_evidence(self) -> None:
        terminal = self.handoff()
        terminal.update({
            "lifecycle_state": "HUMAN_MERGE_READY",
            "lifecycle_event": "DELIVERY_CELL_COMPLETED",
            "from_role": "reviewer",
            "to_role": "coordinator",
            "source_task_key": "issue-1-reviewer",
            "target_task_key": "issue-1-coordinator",
            "recipient_task_keys": ["issue-1-coordinator"],
            "recipient_operation_ids": ["00000000-0000-4000-8000-000000000003"],
            "terminal_state": "completed",
            "sandbox_mode": "read-only",
        })
        self.assertEqual(validate_handoff(terminal), [])
        for name, mutation in {
            "qa changes": lambda value: value["readiness_evidence"].__setitem__("qa_status", "changes_requested"),
            "failed check": lambda value: value["readiness_evidence"].__setitem__("required_checks", [{"name": "validate", "status": "failed"}]),
            "unknown field": lambda value: value["readiness_evidence"].__setitem__("unexpected", True),
        }.items():
            with self.subTest(name=name):
                candidate = copy.deepcopy(terminal)
                mutation(candidate)
                self.assertTrue(validate_handoff(candidate))

    def test_delivery_cell_documentation_uses_current_role_name_and_diagrams(self) -> None:
        legacy_name = "scr" + "ibe"
        matches = []
        for relative in subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines():
            path = ROOT / relative
            try:
                if legacy_name.lower() in path.read_text(encoding="utf-8").lower():
                    matches.append(path)
            except UnicodeDecodeError:
                pass
        self.assertEqual(matches, [])
        coordination = (ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/docs/agentic-sdlc/coordination.md").read_text(encoding="utf-8")
        self.assertIn("KS Knowledge Steward", coordination)
        self.assertIn("flowchart", coordination)
        self.assertIn("stateDiagram-v2", coordination)
        self.assertIn("DELIVERY_CELL_COMPLETED", coordination)

    def test_delivery_unknown_is_child_overlay_and_cannot_retarget_lifecycle(self) -> None:
        evidence = {
            "commit_sha": "a" * 40,
            "pull_request_url": "https://github.com/o/r/pull/6",
            "local_gates": "passed",
            "ci_status": "passed",
            "required_checks": [{"name": "validate", "status": "passed"}],
        }

        def unknown_correction() -> tuple[CoordinatorLifecycle, str]:
            lifecycle = self.lifecycle()
            lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
            for state in (LifecycleState.IMPLEMENTATION_READY, LifecycleState.QA_PASSED, LifecycleState.REVIEW_ACTIVE, LifecycleState.CHANGES_REQUESTED):
                self.peer_transition(lifecycle, state, str(uuid4()), evidence if state is not LifecycleState.CHANGES_REQUESTED else None)
            operation_id = str(uuid4())
            self.peer_unknown(lifecycle, operation_id, LifecycleState.CORRECTION_ACTIVE)
            parent = lifecycle._operations[operation_id]
            return lifecycle, parent.recipient_operation_ids[0]

        lifecycle, child_id = unknown_correction()
        state_before = lifecycle.state
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.observe_delivery_unknown("reviewer", child_id, LifecycleState.BLOCKED)
        self.assertEqual(lifecycle.state, state_before)
        self.assertIn(child_id, lifecycle._unknown)
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", child_id, Reconciliation.UNAVAILABLE), state_before)
        self.assertIn(child_id, lifecycle._unknown)

    def test_lifecycle_duplicate_operations_and_unknown_delivery_cannot_blind_activate(self) -> None:
        lifecycle = self.lifecycle()
        evidence = {"commit_sha": "b" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence)
        self.peer_transition(lifecycle, LifecycleState.QA_PASSED, str(uuid4()), evidence)
        operation_id = str(uuid4())
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.REVIEW_ACTIVE, operation_id, evidence), LifecycleState.REVIEW_ACTIVE)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("reviewer", LifecycleState.REVIEW_ACTIVE, operation_id, evidence)
        with self.assertRaises(LifecycleTransitionError):
            self.peer_transition(lifecycle, LifecycleState.REVIEW_ACCEPTED, operation_id)

        lifecycle = self.lifecycle()
        lifecycle.bind_delivery_artifact(str(uuid4()), evidence["pull_request_url"], evidence["commit_sha"])
        self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, str(uuid4()), evidence)
        self.peer_transition(lifecycle, LifecycleState.QA_PASSED, str(uuid4()), evidence)
        unknown_id = str(uuid4())
        self.assertEqual(self.peer_unknown(lifecycle, unknown_id, LifecycleState.REVIEW_ACTIVE, evidence), LifecycleState.REVIEW_ACTIVE)
        child_id = lifecycle._operations[unknown_id].recipient_operation_ids[0]
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("qa", LifecycleState.REVIEW_ACTIVE, str(uuid4()), evidence)
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", child_id, False), LifecycleState.REVIEW_ACTIVE)
        self.assertEqual(lifecycle.retry_delivery("qa", child_id), DeliveryState.PENDING)

    def test_lifecycle_can_block_when_host_task_control_is_unavailable(self) -> None:
        lifecycle = self.lifecycle()
        operation_id = str(uuid4())
        with self.assertRaises(LifecycleTransitionError):
            self.peer_transition(lifecycle, LifecycleState.BLOCKED, operation_id)
        fallback = {"repository": "o/r", "issue_or_pr_url": "https://github.com/o/r/issues/5", "operation_id": operation_id, "objective": "Recover", "expected_output": "Handoff", "evidence": "PR evidence", "next_owner": "coordinator"}
        self.assertEqual(self.peer_transition(lifecycle, LifecycleState.BLOCKED, operation_id, fallback=fallback), LifecycleState.BLOCKED)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("implementation", LifecycleState.IMPLEMENTATION_READY, str(uuid4()), {})

    def test_lifecycle_handoff_validation_requires_exact_ready_and_review_evidence(self) -> None:
        ready = self.handoff()
        ready.update(
            {
                "lifecycle_state": "IMPLEMENTATION_READY",
                "from_role": "implementation",
                "to_role": "qa",
                "source_task_key": "issue-1-implementation",
                "target_task_key": "issue-1-qa",
                "target_model": "gpt-5.6-sol",
                "effort": "Medium",
                "sandbox_mode": "workspace-write",
                "evidence": ["local gates passed", "CI passed"],
                "readiness_evidence": {"commit_sha": "c" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}], "implementation_evidence": {"role": "implementation", "commit_sha": "c" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}},
                "work_item": {
                    **ready["work_item"],
                    "repository": "o/r",
                    "issue_url": "https://github.com/o/r/issues/1",
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
                "from_role": "qa",
                "source_task_key": "issue-1-qa",
                "target_task_key": "issue-1-reviewer",
                "target_model": "gpt-5.6-sol",
                "effort": "Medium",
                "sandbox_mode": "read-only",
                "work_item": {
                    **reviewer["work_item"],
                    "repository": "o/r",
                    "issue_url": "https://github.com/o/r/issues/1",
                    "pull_request_url": "https://github.com/o/r/pull/6",
                    "commit_sha": "d" * 40,
                },
                "readiness_evidence": {"commit_sha": "d" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}], "implementation_evidence": {"role": "implementation", "commit_sha": "d" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}},
            }
        )
        reviewer["recipient_task_keys"] = ["issue-1-reviewer"]
        reviewer["recipient_operation_ids"] = ["00000000-0000-4000-8000-000000000004"]
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
            "role enum": lambda value: value.__setitem__("to_role", "invalid_role"),
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

    def test_child_delivery_uuid_registry_and_sibling_reconciliation_are_isolated(self) -> None:
        lifecycle = self.lifecycle()
        evidence = {"commit_sha": "f" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        artifact_id = str(uuid4())
        lifecycle.bind_delivery_artifact(artifact_id, evidence["pull_request_url"], evidence["commit_sha"])
        parent_id = str(uuid4())
        qa_child, reviewer_child = str(uuid4()), str(uuid4())
        self.peer_transition(
            lifecycle,
            LifecycleState.IMPLEMENTATION_READY,
            parent_id,
            evidence,
            recipient_operation_ids={"issue-5-qa": qa_child, "issue-5-reviewer": reviewer_child},
        )
        self.assertEqual(lifecycle._operations[qa_child].parent_operation_id, parent_id)
        self.assertEqual(lifecycle._operations[reviewer_child].recipient_task_key, "issue-5-reviewer")
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.observe_delivery_unknown("implementation", reviewer_child)
        lifecycle.observe_delivery_unknown("implementation", reviewer_child, target_task_key="issue-5-reviewer")
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", reviewer_child, Reconciliation.ABSENT), LifecycleState.IMPLEMENTATION_READY)
        self.assertEqual(lifecycle._operations[qa_child].state, DeliveryState.PENDING)
        self.assertEqual(lifecycle.retry_delivery("implementation", reviewer_child), DeliveryState.PENDING)
        lifecycle.observe_delivery_unknown("implementation", reviewer_child, target_task_key="issue-5-reviewer")
        self.assertEqual(lifecycle.reconcile_delivery("coordinator", reviewer_child, Reconciliation.APPLIED), LifecycleState.IMPLEMENTATION_READY)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.observe_delivery_unknown("implementation", reviewer_child)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("qa", LifecycleState.QA_PASSED, artifact_id, evidence)
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("qa", LifecycleState.QA_PASSED, str(uuid4()), evidence, recipient_operation_ids={"issue-5-reviewer": qa_child, "issue-5-implementation": str(uuid4())})

    def test_structured_evidence_requires_current_role_bound_artifact_without_side_effects(self) -> None:
        lifecycle = self.lifecycle()
        base = {"commit_sha": "c" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.bind_delivery_artifact(str(uuid4()), base["pull_request_url"], base["commit_sha"])
        bad = {**base, "implementation_evidence": {"role": "implementation", **base, "commit_sha": "d" * 40}}
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("implementation", LifecycleState.IMPLEMENTATION_READY, str(uuid4()), bad, source_task_key="issue-5-implementation", target_task_key="issue-5-qa", recipient_operation_ids={"issue-5-qa": str(uuid4()), "issue-5-reviewer": str(uuid4())})
        self.assertEqual(lifecycle.state, LifecycleState.IMPLEMENTATION_ACTIVE)
        self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, str(uuid4()), base)
        malformed = {**base, "qa_evidence": {"role": "qa", **base, "required_checks": []}}
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("qa", LifecycleState.QA_PASSED, str(uuid4()), malformed, source_task_key="issue-5-qa", target_task_key="issue-5-reviewer", recipient_operation_ids={"issue-5-reviewer": str(uuid4()), "issue-5-implementation": str(uuid4())})
        self.assertEqual(lifecycle.state, LifecycleState.IMPLEMENTATION_READY)
        review_only = {**base, "review_evidence": {"role": "reviewer", **base}}
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("reviewer", LifecycleState.REVIEW_ACCEPTED, str(uuid4()), review_only, source_task_key="issue-5-reviewer", target_task_key="issue-5-reviewer", recipient_operation_ids={"issue-5-reviewer": str(uuid4())})
        self.assertEqual(lifecycle.state, LifecycleState.IMPLEMENTATION_READY)

    def test_terminal_cell_evidence_requires_matching_recorded_qa_and_reviewer_proof(self) -> None:
        lifecycle = self.lifecycle()
        base = {"commit_sha": "e" * 40, "pull_request_url": "https://github.com/o/r/pull/6", "local_gates": "passed", "ci_status": "passed", "required_checks": [{"name": "validate", "status": "passed"}]}
        lifecycle.bind_delivery_artifact(str(uuid4()), base["pull_request_url"], base["commit_sha"])
        self.peer_transition(lifecycle, LifecycleState.IMPLEMENTATION_READY, str(uuid4()), base)
        self.peer_transition(lifecycle, LifecycleState.QA_PASSED, str(uuid4()), base)
        self.peer_transition(lifecycle, LifecycleState.REVIEW_ACTIVE, str(uuid4()), base)
        stale = {**base, "review_evidence": {"role": "reviewer", **base, "commit_sha": "f" * 40}}
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("reviewer", LifecycleState.REVIEW_ACCEPTED, str(uuid4()), stale, source_task_key="issue-5-reviewer", target_task_key="issue-5-reviewer", recipient_operation_ids={"issue-5-reviewer": str(uuid4())})
        self.assertEqual(lifecycle.state, LifecycleState.REVIEW_ACTIVE)
        self.peer_transition(lifecycle, LifecycleState.REVIEW_ACCEPTED, str(uuid4()), base)
        terminal_missing = {**base, "implementation_evidence": {"role": "implementation", **base}}
        with self.assertRaises(LifecycleTransitionError):
            lifecycle.transition("reviewer", LifecycleState.HUMAN_MERGE_READY, str(uuid4()), terminal_missing, source_task_key="issue-5-reviewer", target_task_key="issue-5-coordinator", recipient_operation_ids={"issue-5-coordinator": str(uuid4())})
        self.assertEqual(lifecycle.state, LifecycleState.REVIEW_ACCEPTED)

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
        with self.assertRaises(LifecycleTransitionError):
            operation.reconcile(Reconciliation.APPLIED)
        self.assertEqual(operation.state, DeliveryState.NOT_DELIVERED)
        self.assertEqual(operation.retry(), DeliveryState.PENDING)
        self.assertEqual(operation.observe(Observation.DELIVERED_AND_ACKNOWLEDGED, authoritative_confirmed=True), DeliveryState.DELIVERED)

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

    def qa_repository(self) -> tuple[Path, str]:
        temporary = ROOT / "tests" / ".tmp" / str(uuid4())
        temporary.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, temporary, ignore_errors=True)
        source = temporary / "implementation"
        source.mkdir()
        def run(cwd: Path, *args: str) -> str:
            result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        run(source, "init")
        run(source, "config", "user.email", "implementation@example.com")
        run(source, "config", "user.name", "Implementation")
        (source / "source.txt").write_text("source\n", encoding="utf-8")
        run(source, "add", "source.txt")
        run(source, "commit", "-m", "initial")
        run(source, "branch", "-M", "main")
        sha = run(source, "rev-parse", "HEAD")
        target = temporary / "qa"
        clone = subprocess.run(["git", "clone", str(source), str(target)], text=True, capture_output=True, check=False)
        self.assertEqual(clone.returncode, 0, clone.stderr)
        run(target, "config", "user.email", "qa@example.com")
        run(target, "config", "user.name", "QA")
        run(target, "checkout", "--detach", sha)
        if not hasattr(self, "_qa_sources"):
            self._qa_sources: dict[Path, Path] = {}
        self._qa_sources[target] = source
        return target, sha

    def run_qa_cli(
        self, target: Path, sha: str, code: str, *options: str, implementation_branch: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        source = self._qa_sources[target]
        command = [
            sys.executable,
            str(ROOT / "scripts" / "qa_workspace.py"),
            str(target),
            "--expected-sha",
            sha,
            "--implementation-branch",
            implementation_branch,
            "--implementation-repository",
            str(source),
            *options,
            "--",
            sys.executable,
            "-c",
            code,
        ]
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def qa_source(self, target: Path) -> Path:
        return self._qa_sources[target]

    def test_qa_cli_executes_shell_free_command_and_reports_both_snapshots(self) -> None:
        target, sha = self.qa_repository()
        result = self.run_qa_cli(target, sha, "pass")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["before"]["head"], sha)
        self.assertEqual(payload["after"]["head"], sha)
        self.assertEqual(payload["exit_code"], 0)
        self.assertTrue(payload["command"])
        self.assertEqual(payload["before"]["branch_refs"], payload["after"]["branch_refs"])
        self.assertEqual(payload["before"]["implementation_ref"], ["refs/heads/main", sha])
        self.assertNotEqual(payload["before"]["git_common_dir"], str((self.qa_source(target) / ".git").resolve()))

    def test_qa_cli_uses_isolated_clone_and_ignores_unrelated_source_ref_change(self) -> None:
        target, sha = self.qa_repository()
        source = self.qa_source(target)
        code = f"import subprocess; subprocess.run(['git','-C',{str(source)!r},'branch','unrelated-source-branch'], check=True)"
        result = self.run_qa_cli(target, sha, code)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertNotEqual(payload["before"]["git_common_dir"], str((source / ".git").resolve()))
        self.assertEqual(payload["before"]["branch_refs"], payload["after"]["branch_refs"])

    def test_qa_cli_rejects_missing_wrong_and_moved_implementation_ref(self) -> None:
        target, sha = self.qa_repository()
        missing = self.run_qa_cli(target, sha, "pass", implementation_branch="missing")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("implementation ref", missing.stdout)
        target, sha = self.qa_repository()
        wrong = self.run_qa_cli(
            target,
            sha,
            f"import subprocess; subprocess.run(['git','commit','--allow-empty','-m','temporary'], check=True); new=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(); subprocess.run(['git','update-ref','refs/heads/main',new], check=True); subprocess.run(['git','reset','--hard','{sha}'], check=True)",
        )
        self.assertEqual(wrong.returncode, 2)
        self.assertIn("implementation ref", wrong.stdout)
        target, sha = self.qa_repository()
        subprocess.run(["git", "commit", "--allow-empty", "-m", "moved-before-qa"], cwd=target, check=True, capture_output=True)
        moved_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=target, text=True, capture_output=True, check=True).stdout.strip()
        subprocess.run(["git", "update-ref", "refs/heads/main", moved_sha], cwd=target, check=True, capture_output=True)
        subprocess.run(["git", "reset", "--hard", sha], cwd=target, check=True, capture_output=True)
        moved = self.run_qa_cli(target, sha, "pass")
        self.assertEqual(moved.returncode, 2)
        self.assertIn("implementation ref", moved.stdout)

    def test_qa_cli_rejects_create_then_detach_branch_ref_change(self) -> None:
        target, sha = self.qa_repository()
        code = f"import subprocess; subprocess.run(['git','branch','qa-scratch'], check=True); subprocess.run(['git','checkout','--detach','{sha}'], check=True)"
        result = self.run_qa_cli(target, sha, code)
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertIn("refs/heads", " ".join(payload["errors"]))
        self.assertNotEqual(payload["before"]["branch_refs"], payload["after"]["branch_refs"])

    def test_qa_cli_rejects_moved_ref_after_restoring_detached_head(self) -> None:
        target, sha = self.qa_repository()
        code = f"import subprocess; subprocess.run(['git','commit','--allow-empty','-m','moved'], check=True); new=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(); subprocess.run(['git','update-ref','refs/heads/main',new], check=True); subprocess.run(['git','reset','--hard','{sha}'], check=True)"
        result = self.run_qa_cli(target, sha, code)
        self.assertEqual(result.returncode, 2)
        self.assertIn("refs/heads", result.stdout)

    def test_qa_cli_rejects_local_branch_delete_and_rename(self) -> None:
        target, sha = self.qa_repository()
        deleted = self.run_qa_cli(target, sha, "import subprocess; subprocess.run(['git','branch','-D','main'], check=True)")
        self.assertEqual(deleted.returncode, 2)
        self.assertIn("refs/heads", deleted.stdout)
        target, sha = self.qa_repository()
        renamed = self.run_qa_cli(target, sha, "import subprocess; subprocess.run(['git','branch','-m','main','renamed'], check=True)")
        self.assertEqual(renamed.returncode, 2)
        self.assertIn("refs/heads", renamed.stdout)

    def test_qa_cli_allows_declared_output_but_rejects_preexisting_source(self) -> None:
        target, sha = self.qa_repository()
        result = self.run_qa_cli(target, sha, "from pathlib import Path; Path('cache').mkdir(); Path('cache/result.txt').write_text('ok')", "--declared-output", "cache")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (target / "source.txt").write_text("dirty\n", encoding="utf-8")
        blocked = self.run_qa_cli(target, sha, "from pathlib import Path; Path('ran').write_text('no')")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("pre-existing", blocked.stdout)
        self.assertFalse((target / "ran").exists())

    def test_qa_cli_rejects_source_head_branch_and_failing_command(self) -> None:
        target, sha = self.qa_repository()
        mutated = self.run_qa_cli(target, sha, "from pathlib import Path; Path('source.txt').write_text('mutated')")
        self.assertEqual(mutated.returncode, 2)
        self.assertIn("source", mutated.stdout.lower())
        target, sha = self.qa_repository()
        committed = self.run_qa_cli(target, sha, "import subprocess; subprocess.run(['git','commit','--allow-empty','-m','mutated'], check=True)")
        self.assertEqual(committed.returncode, 2)
        self.assertIn("HEAD", committed.stdout)
        target, sha = self.qa_repository()
        attached = self.run_qa_cli(target, sha, "import subprocess; subprocess.run(['git','switch','-c','qa-scratch'], check=True)")
        self.assertEqual(attached.returncode, 2)
        self.assertIn("detached", attached.stdout)
        target, sha = self.qa_repository()
        failed = self.run_qa_cli(target, sha, "raise SystemExit(3)")
        self.assertEqual(failed.returncode, 2)
        self.assertIn("exit code 3", failed.stdout)

    def test_qa_cli_rejects_tracked_declarations_staging_deletion_and_rename(self) -> None:
        target, sha = self.qa_repository()
        declared_file = self.run_qa_cli(target, sha, "pass", "--declared-output", "source.txt")
        self.assertEqual(declared_file.returncode, 2)
        self.assertIn("overlaps tracked", declared_file.stdout)
        target, sha = self.qa_repository()
        staged = self.run_qa_cli(target, sha, "from pathlib import Path; Path('source.txt').write_text('staged'); import subprocess; subprocess.run(['git','add','source.txt'], check=True)")
        self.assertEqual(staged.returncode, 2)
        self.assertIn("source", staged.stdout.lower())
        target, sha = self.qa_repository()
        deleted = self.run_qa_cli(target, sha, "import subprocess; subprocess.run(['git','rm','source.txt'], check=True)")
        self.assertEqual(deleted.returncode, 2)
        self.assertIn("source", deleted.stdout.lower())
        target, sha = self.qa_repository()
        renamed = self.run_qa_cli(target, sha, "import subprocess; subprocess.run(['git','mv','source.txt','renamed.txt'], check=True)")
        self.assertEqual(renamed.returncode, 2)
        self.assertIn("source", renamed.stdout.lower())

    def test_qa_cli_rejects_declared_directory_containing_tracked_source(self) -> None:
        target, sha = self.qa_repository()
        (target / "tracked").mkdir()
        (target / "tracked" / "source.txt").write_text("source\n", encoding="utf-8")
        subprocess.run(["git", "rm", "source.txt"], cwd=target, check=True, capture_output=True)
        subprocess.run(["git", "add", "tracked/source.txt"], cwd=target, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "nest source"], cwd=target, check=True, capture_output=True)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=target, text=True, capture_output=True, check=True).stdout.strip()
        subprocess.run(["git", "checkout", "--detach", sha], cwd=target, check=True, capture_output=True)
        result = self.run_qa_cli(target, sha, "pass", "--declared-output", "tracked")
        self.assertEqual(result.returncode, 2)
        self.assertIn("overlaps tracked", result.stdout)

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
        for relative in ("scripts/coordination_protocol.py", "scripts/validate_handoff.py", "scripts/qa_workspace.py"):
            self.assertTrue((target / relative).is_file(), relative)
        config = (target / ".agentic-sdlc/config.yaml").read_text(encoding="utf-8")
        self.assertIn('project_name: "Legacy Fixture"', config)
        self.assertIn('session_title_format: "#{issue_number} {role_code} - {issue_title}"', config)
        self.assertIn("routing_policy: repository-native-v1", config)
        self.assertNotIn("{project_name} #{issue_number}", config)
        again, _, _ = manage_repository.plan(target, "Legacy Fixture")
        self.assertFalse([a for a in again if a.classification in {"create", "update", "delete", "managed-block-update", "conflict"}])

    def test_fresh_bootstrap_installs_runtime_cli_and_blocks_invalid_dispatch(self) -> None:
        temporary = ROOT / "tests" / ".tmp" / str(uuid4())
        temporary.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, temporary)
        target = temporary / "fresh"
        target.mkdir()
        actions, writes, obsolete = manage_repository.plan(target, "Fresh Fixture")
        self.assertFalse([action for action in actions if action.classification == "conflict"])
        manage_repository.apply(target, writes, obsolete, "Fresh Fixture")
        for relative in ("scripts/coordination_protocol.py", "scripts/validate_handoff.py", "scripts/qa_workspace.py"):
            self.assertTrue((target / relative).is_file(), relative)
        handoff_path = target / ".agentic-sdlc/handoff-template.json"
        valid = json.loads(handoff_path.read_text(encoding="utf-8"))
        cli = target / "scripts/validate_handoff.py"
        accepted = subprocess.run([sys.executable, str(cli)], input=json.dumps(valid), text=True, capture_output=True, cwd=target, check=False)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        invalid = copy.deepcopy(valid)
        invalid["objective"] = ""
        dispatch_probe = "import json,sys; sys.path.insert(0, 'scripts'); from coordination_protocol import dispatch_issue_task; dispatch_issue_task(json.loads(sys.stdin.read()), lambda _: print('DISPATCH_CALLED'))"
        rejected = subprocess.run([sys.executable, "-c", dispatch_probe], input=json.dumps(invalid), text=True, capture_output=True, cwd=target, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("blocked by invalid handoff", rejected.stderr)
        self.assertNotIn("DISPATCH_CALLED", rejected.stdout + rejected.stderr)
        actions, _, _ = manage_repository.plan(target, "Fresh Fixture")
        self.assertFalse([action for action in actions if action.classification in {"create", "update", "delete", "managed-block-update", "conflict"}])
        runtime = target / "scripts/qa_workspace.py"
        qa_target, qa_sha = self.qa_repository()
        installed_cli = subprocess.run(
            [
                sys.executable,
                str(runtime),
                str(qa_target),
                "--expected-sha",
                qa_sha,
                "--implementation-branch",
                "main",
                "--implementation-repository",
                str(self.qa_source(qa_target)),
                "--",
                sys.executable,
                "-c",
                "pass",
            ],
            text=True,
            capture_output=True,
            cwd=target,
            check=False,
        )
        self.assertEqual(installed_cli.returncode, 0, installed_cli.stdout + installed_cli.stderr)
        installed_payload = json.loads(installed_cli.stdout)
        self.assertEqual(installed_payload["before"]["head"], qa_sha)
        self.assertEqual(installed_payload["after"]["head"], qa_sha)
        runtime.write_text(runtime.read_text(encoding="utf-8") + "\n# local drift\n", encoding="utf-8")
        actions, _, _ = manage_repository.plan(target, "Fresh Fixture")
        self.assertIn(("conflict", "scripts/qa_workspace.py"), {(a.classification, a.path.as_posix()) for a in actions})

    def test_windows_crlf_noop_apply_preserves_managed_manifest_bytes(self) -> None:
        target, _ = self.apply_fixture("customized_repository", "Customized Fixture")
        manifest = target / ".agentic-sdlc/managed.json"
        crlf = manifest.read_bytes().replace(b"\n", b"\r\n")
        manifest.write_bytes(crlf)
        actions, writes, obsolete = manage_repository.plan(target, "Customized Fixture")
        self.assertFalse([a for a in actions if a.classification in {"create", "update", "delete", "managed-block-update", "conflict"}])
        manage_repository.apply(target, writes, obsolete, "Customized Fixture")
        self.assertEqual(manifest.read_bytes(), crlf)
        check, _, _ = manage_repository.plan(target, "Customized Fixture")
        self.assertFalse([a for a in check if a.classification != "unchanged" and a.path == manage_repository.MANIFEST_PATH])

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
