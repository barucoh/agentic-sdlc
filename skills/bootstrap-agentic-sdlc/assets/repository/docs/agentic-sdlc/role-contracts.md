<!-- agentic-sdlc:managed role-contracts/v1 -->
# Role contracts

The executable contracts are the standalone files in `.codex/agents/`. Each declares its inputs, outputs, terminal states, escalation conditions, forbidden actions, permission posture, and authority. The `name` field is the discovery source of truth.

| Role | Owns | Permission posture | Separation boundary |
|---|---|---|---|
| Coordinator | Routing, sequencing, reconciliation, readiness synthesis | Read-only repository | Does not implement, verify, approve, or merge |
| Product | User value, behavior, acceptance criteria | Read-only | Does not decide architecture or approve delivery |
| Architecture | Boundaries and architecture constraints | Read-only | Uses `adr-context`; does not implement or override ADRs |
| Implementation | Scoped changes and corrections | Workspace-write in its issue worktree | Never approves or merges its own work |
| QA | Independent behavioral verification | Read-only against implementation | Does not modify implementation or review-approve |
| Reviewer | Independent correctness and risk review | Read-only against implementation | Does not modify, self-review, or merge |
| Knowledge Steward | Canonical documentation and ADR hygiene | Workspace-write in its issue worktree | Preserves Product/Architecture decision authority; no Scribe duplicate |

Roles that write or make material decisions run only in issue-backed user-visible tasks. An agent file is a reusable contract, not permission to invoke that role as an ephemeral subagent. Ephemeral subagents are restricted to bounded read-only information gathering.

All roles use `.agentic-sdlc/handoff.schema.json`. Handoff and ADR algorithms are centralized in the schema, `docs/agentic-sdlc/coordination.md`, and `adr-context`, not repeated in individual role files.
