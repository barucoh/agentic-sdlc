---
name: coordinate-agentic-sdlc
description: Coordinate Agentic SDLC role work, durable issue-backed sessions, structured handoffs, correction loops, and resilient cross-task delivery. Use when routing work among Agentic SDLC roles or recovering uncertain cross-task delivery.
---

# Coordinate Agentic SDLC

1. Read the authoritative issue, applicable `AGENTS.md`, `.agentic-sdlc/config.yaml`, and `docs/agentic-sdlc/role-contracts.md`.
2. Route ADR discovery only through `adr-context`.
3. Classify execution before delegation:
   - Use one delivery cell per issue: one writable Implementation worktree/branch and one PR, with distinct issue-backed user-visible Coordinator, Implementation, QA, and Reviewer tasks. Only Implementation writes source or commits; QA and Reviewer are independent and non-authoritative.
   - Use an ephemeral subagent only for bounded read-only research, discovery, log analysis, documentation lookup, or evidence gathering. Require `sandbox_mode = "read-only"`, prohibit file and external-state changes, and request concise evidence.
4. Name every new durable task `#<issue number> <role code> - <issue title>` using `CO`, `PD`, `AR`, `IM`, `QA`, `RV`, or `KS`. Reject unknown codes. The complete title is at most 36 Unicode characters: build the prefix first, then if necessary truncate only the issue-title segment and end with one `…`. Do not include `project_name` or automatically rename existing sessions.
5. Create a UUID operation ID and a handoff conforming to `.agentic-sdlc/handoff.schema.json` for every cross-task action.
6. Before every issue-backed task creation call `scripts.coordination_protocol.dispatch_issue_task`; before every required cross-task send call `send_cross_task_handoff`. Both use canonical `validate_handoff`, return concise schema/lifecycle errors, and invoke the transport only after validation succeeds. Pass `target_model`, `effort`, and a one-sentence `rationale` explicitly when creating or activating every durable handoff. Use the repository-native routing matrix: Sol/Medium for Coordinator, Product, Architecture, QA, and Reviewer defaults; Luna/Low for straightforward Implementation and routine Knowledge Steward work; Terra only for nontrivial Implementation with explicit risk/complexity justification for higher effort. Reviewer activation is always Sol/Medium or justified Sol/High.
7. Persist the requested outcome in an authoritative GitHub issue, PR, review comment, commit, ADR, or canonical document. A message is only an optional wake-up optimization.
8. Send any optional wake-up with a 20–30 second watchdog, defaulting to the configured 25 seconds, and retain independent state per target.
9. Treat timeout, handler failure, and delivered-but-acknowledgement-failed as `DELIVERY_UNKNOWN`, not failure. A later transport observation cannot make it retryable. Record reconciliation of the exact operation ID against the target task and GitHub before retrying; only recorded `ABSENT` permits retry. Delivered or applied operations are terminal and cannot be reopened or repeated. If reconciliation is unavailable, preserve uncertainty and stop.
10. Provide a GitHub-reconstructible copy/paste fallback containing operation ID, issue or PR URL, objective, expected output, evidence, and next owner. Never persist thread IDs.
11. Establish a native delivery cell: Coordinator binds the IM, QA, and RV user-visible tasks and their machine-local handles, then supervises rather than relays. IM sends IMPLEMENTATION_READY directly to QA and RV; QA sends QA_PASSED or QA_CHANGES_REQUESTED directly to RV and IM; RV leads verification, returns CHANGES_REQUESTED directly to the same IM, and emits DELIVERY_CELL_COMPLETED/HUMAN_MERGE_READY to Coordinator only after QA pass and clean independent review at the exact SHA.

12. Coordinator receives routine status from durable GitHub evidence, and direct peer messages are optional wake-ups. Return to Coordinator early only for BLOCKED, escalation, or DELIVERY_UNKNOWN. Reconcile the exact UUID before retrying unknown delivery; never duplicate an applied peer transition. If task control is unavailable, stop as blocked with a reconstructible fallback.

QA runs behavioral commands in an isolated disposable workspace-write QA worktree derived from the implementation commit. It may create caches, builds, and test outputs there, but source mutation, commits, pushes, and implementation-branch changes are forbidden; invoke the repository QA workspace check for exact-head and before/after drift evidence. Codex host/task-control remains responsible for provisioning and removing the disposable worktree; report blocked if cleanup is unavailable or drift is detected.

Use only `completed`, `changes_requested`, or `blocked` as handoff terminal states. `DELIVERY_UNKNOWN` is transport state only.
