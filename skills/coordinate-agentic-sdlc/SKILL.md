---
name: coordinate-agentic-sdlc
description: Coordinate Agentic SDLC role work, durable issue-backed sessions, structured handoffs, correction loops, and resilient cross-task delivery. Use when routing work among Agentic SDLC roles or recovering uncertain cross-task delivery.
---

# Coordinate Agentic SDLC

1. Read the authoritative issue, applicable `AGENTS.md`, `.agentic-sdlc/config.yaml`, and `docs/agentic-sdlc/role-contracts.md`.
2. Route ADR discovery only through `adr-context`.
3. Classify execution before delegation:
   - Use a separate issue-backed user-visible task, worktree, branch, and PR for any file write, durable artifact, material decision, release action, high-importance action, or risk-bearing action.
   - Use an ephemeral subagent only for bounded read-only research, discovery, log analysis, documentation lookup, or evidence gathering. Require `sandbox_mode = "read-only"`, prohibit file and external-state changes, and request concise evidence.
4. Name every new durable task `#<issue number> <role code> - <issue title>` using `CO`, `PD`, `AR`, `IM`, `QA`, `RV`, or `KS`. Reject unknown codes. The complete title is at most 36 Unicode characters: build the prefix first, then if necessary truncate only the issue-title segment and end with one `…`. Do not include `project_name` or automatically rename existing sessions.
5. Create a UUID operation ID and a handoff conforming to `.agentic-sdlc/handoff.schema.json` for every cross-task action.
6. Pass `target_model`, `effort`, and a one-sentence `rationale` explicitly when creating or activating every durable handoff. Use the repository-native routing matrix: Sol/Medium for Coordinator, Product, Architecture, QA, and Reviewer defaults; Luna/Low for straightforward Implementation and routine Knowledge Steward work; Terra only for nontrivial Implementation with explicit risk/complexity justification for higher effort. Reviewer activation is always Sol/Medium or justified Sol/High.
7. Persist the requested outcome in an authoritative GitHub issue, PR, review comment, commit, ADR, or canonical document. A message is only an optional wake-up optimization.
8. Send any optional wake-up with a 20–30 second watchdog, defaulting to the configured 25 seconds, and retain independent state per target.
9. Treat timeout, handler failure, and delivered-but-acknowledgement-failed as `DELIVERY_UNKNOWN`, not failure. A later transport observation cannot make it retryable. Record reconciliation of the exact operation ID against the target task and GitHub before retrying; only recorded `ABSENT` permits retry. Delivered or applied operations are terminal and cannot be reopened or repeated. If reconciliation is unavailable, preserve uncertainty and stop.
10. Provide a GitHub-reconstructible copy/paste fallback containing operation ID, issue or PR URL, objective, expected output, evidence, and next owner. Never persist thread IDs.
11. Send QA or Reviewer corrections back to the same Implementation task using an allowed Luna/Terra implementation route. Reviewer activation explicitly uses Sol/Medium or justified Sol/High. Require a finding-to-fix verification map, then repeat independent verification. Implementation cannot approve itself.

Use only `completed`, `changes_requested`, or `blocked` as handoff terminal states. `DELIVERY_UNKNOWN` is transport state only.
