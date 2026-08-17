<!-- agentic-sdlc:managed coordination/v1 -->
# Coordination and delivery resilience

## Durable authority

GitHub issues, pull requests, commits, review comments, ADRs, and committed canonical documentation are authoritative. Chat or cross-task messages are transport only. Thread IDs are machine-local hints and must never be committed or used as the only recovery key.

Any file-producing, durable-artifact-producing, decision-heavy, release, high-importance, or risk-bearing task uses one authoritative issue, one user-visible task, one worktree, one branch, and one pull request. Ephemeral subagents are limited to bounded read-only research, discovery, log analysis, documentation lookup, or evidence gathering. They must use a read-only sandbox, make no file or external-state changes, and return concise evidence.

## Session titles

Name every new user-visible task `#<issue number> <role code> - <issue title>`. The stable codes are `CO` Coordinator, `PD` Product, `AR` Architecture, `IM` Implementation, `QA` QA, `RV` Reviewer, and `KS` Knowledge Steward. Reject unknown codes rather than inventing one.

The complete title may contain at most 36 Unicode characters, including the prefix, spaces, hyphen, and ellipsis. Build `#<issue_number> <role_code> - ` first. If the complete title is too long, truncate only the issue-title segment and end it with one Unicode ellipsis `…`. Do not include `project_name`; it remains available for other repository metadata. Apply this rule prospectively. Repository migrations do not rename existing sessions unless a user explicitly requests it.

## Cross-task operation protocol

Every durable handoff includes explicit `target_model`, `effort`, and one-sentence `rationale`; task creation and activation must pass them explicitly and may not inherit a coordinator or system default. The repository-native matrix is encoded in the handoff validator and `.agentic-sdlc/config.yaml`: decision/orchestration roles use Sol/Medium, routine implementation and Knowledge Steward work use Luna/Low, nontrivial implementation may use Terra with explicit risk/complexity justification for higher effort, and QA/Reviewer use Sol/Medium with explicit high-risk rationale for High. Ephemeral research is always read-only and uses Luna/Low by default, Terra/Low for unusually complex synthesis, or Sol only with explicit exceptional rationale.

1. Generate one UUID operation ID before every cross-task action and include it in the versioned handoff.
   Replace the zero UUID in `handoff-template.json`; it is a schema-valid placeholder, never an operation ID to reuse.
2. Persist the requested outcome in an authoritative GitHub issue, PR, review comment, or commit before relying on an optional wake-up message.
3. Track state independently per target. Never let one target acknowledgement complete or fail another target.
4. Send the optional message with a bounded 20–30 second watchdog; the configured default is 25 seconds.
5. Acknowledgement completes transport only after the authoritative state is confirmed.
6. Explicit authoritative non-delivery may be reported as not delivered only before uncertainty exists. Timeout, handler failure, or delivered-but-acknowledgement-failed yields `DELIVERY_UNKNOWN`, never `FAILED`; a later transport observation cannot make that operation retryable.
7. Before retrying any side effect, record reconciliation of the exact operation ID against the target task and authoritative GitHub state. Only a recorded `ABSENT` result may transition `DELIVERY_UNKNOWN` to retryable `NOT_DELIVERED`. If already applied, record terminal success without repeating it. A delivered or applied operation is immutable and cannot be reopened. If reconciliation is unavailable or ambiguous, stop and preserve `DELIVERY_UNKNOWN`.
8. Preserve the per-target operation record and provide a copy/paste fallback containing repository, issue/PR URL, operation ID, objective, expected output, evidence, and next owner. The fallback must be reconstructible from GitHub without a thread ID.

Duplicate operation IDs are idempotency keys. A target must not perform the same side effect twice.

## Correction loop

QA or Reviewer returns `changes_requested` through Coordinator. Every finding identifies an ID, evidence, expected correction, and verification. Coordinator sends the correction to the same issue-backed Implementation task. Implementation returns a finding-to-fix map with changed files or commits and exact verification. QA and Reviewer then independently re-check their findings. Implementation never approves its own correction.

## Terminal and transport states

Handoff terminal states are `completed`, `changes_requested`, and `blocked`. Transport may additionally report `DELIVERY_UNKNOWN`. Transport uncertainty does not change the underlying work state and must be reconciled before side effects are retried.
