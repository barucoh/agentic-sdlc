<!-- agentic-sdlc:managed role-contracts/v1 -->
# Role contracts

The executable contracts are the standalone files in `.codex/agents/`. Each declares its inputs, outputs, terminal states, escalation conditions, forbidden actions, permission posture, and authority. The `name` field is the discovery source of truth.

| Role | Code | Owns | Permission posture | Separation boundary |
|---|---|---|---|---|
| Coordinator | CO | Routing, sequencing, reconciliation, readiness synthesis | Read-only repository | Does not implement, verify, approve, or merge |
| Product | PD | User value, behavior, acceptance criteria | Read-only | Does not decide architecture or approve delivery |
| Architecture | AR | Boundaries and architecture constraints | Read-only | Uses `adr-context`; does not implement or override ADRs |
| Implementation | IM | Scoped changes and corrections | Workspace-write in its issue worktree | Never approves or merges its own work |
| QA | QA | Independent behavioral verification | Isolated disposable workspace-write worktree | May create test outputs, but does not modify source, commit, push, or review-approve |
| Reviewer | RV | Independent correctness and risk review | Read-only against implementation | Does not modify, self-review, or merge |
| Knowledge Steward | KS | Canonical documentation and ADR hygiene | Workspace-write in its issue worktree | Preserves Product/Architecture decision authority |

The role codes are authoritative for prospective session titles. Unknown codes are invalid.

Model routing is also authoritative: Coordinator/Product/Architecture use Sol/Medium by default; Implementation uses Luna/Low or Terra for nontrivial work; QA/Reviewer use Sol/Medium; Knowledge Steward uses Luna/Low. Every handoff carries explicit Target model, Effort, and one-sentence Rationale. Corrections return to the same Implementation task using Luna or Terra; Reviewer activation uses Sol/Medium or justified Sol/High.

Roles that write or make material decisions run only in issue-backed user-visible tasks. An agent file is a reusable contract, not permission to invoke that role as an ephemeral subagent. Ephemeral subagents are restricted to bounded read-only information gathering.

All roles use `.agentic-sdlc/handoff.schema.json`. Handoff and ADR algorithms are centralized in the schema, `docs/agentic-sdlc/coordination.md`, and `adr-context`, not repeated in individual role files.
