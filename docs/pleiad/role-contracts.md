<!-- pleiad:managed role-contracts/v1 -->
# Role contracts

The executable contracts are the standalone files in `.codex/agents/`. Each declares its inputs, outputs, terminal states, escalation conditions, forbidden actions, permission posture, and authority. The `name` field is the discovery source of truth.

| Role | Code | Owns | Permission posture | Separation boundary |
|---|---|---|---|---|
| Coordinator | CO | Routing, sequencing, reconciliation, readiness synthesis | Read-only repository | Does not implement, verify, approve, or merge |
| Product | PD | User value, behavior, acceptance criteria | Read-only | Does not decide architecture or approve delivery |
| Architecture | AR | Boundaries and architecture constraints | Read-only | Uses `adr-context`; does not implement or override ADRs |
| Implementation | IM | Scoped changes and corrections | Workspace-write in its issue worktree | Never approves or merges its own work |
| QA | QA | Independent behavioral verification | Host-provisioned isolated disposable workspace-write clone | May create test outputs, but does not modify source, commit, push, local refs, or review-approve |
| Reviewer | RV | Independent correctness and risk review | Read-only against implementation | Does not modify, self-review, or merge |
| Knowledge Steward | KS | Canonical documentation and ADR hygiene | Workspace-write in its issue worktree | Preserves Product/Architecture decision authority |

The role codes are authoritative for prospective session titles. Unknown codes are invalid.

Model routing is authoritative through the effective validated project policy. Built-in defaults use Luna for straightforward implementation, Terra for nontrivial implementation, Sol for difficult implementation and coordination/design/QA/review work; QA and Reviewer defaults are deterministically Sol/Medium. Every handoff carries explicit Target model, Effort, and one-sentence Rationale. The recommended Astra route is exceptional/High effort; a project may explicitly permit another supported effort, but Astra is never a routine default and every use requires a structured explanation of why Sol is insufficient. Projects can disable Astra. Routing choices cannot change role authority, sandbox, evidence, correction, review, or human-merge constraints.

Roles that write or make material decisions run only in issue-backed user-visible tasks. An agent file is a reusable contract, not permission to invoke that role as an ephemeral subagent. Ephemeral subagents are restricted to bounded read-only information gathering.

All roles use `.pleiad/handoff.schema.json`. Handoff and ADR algorithms are centralized in the schema, `docs/pleiad/coordination.md`, and `adr-context`, not repeated in individual role files.
