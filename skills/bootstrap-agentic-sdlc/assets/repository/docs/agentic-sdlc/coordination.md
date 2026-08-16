<!-- agentic-sdlc:managed coordination/v1 -->
# Coordination and delivery resilience

## Durable authority

GitHub issues, pull requests, commits, review comments, ADRs, and committed canonical documentation are authoritative. Chat or cross-task messages are transport only. Thread IDs are machine-local hints and must never be committed or used as the only recovery key.

Any file-producing, durable-artifact-producing, decision-heavy, release, high-importance, or risk-bearing task uses one authoritative issue, one user-visible task, one worktree, one branch, and one pull request. Ephemeral subagents are limited to bounded read-only research, discovery, log analysis, documentation lookup, or evidence gathering. They must use a read-only sandbox, make no file or external-state changes, and return concise evidence.

## Cross-task operation protocol

1. Generate one UUID operation ID before every cross-task action and include it in the versioned handoff.
   Replace the zero UUID in `handoff-template.json`; it is a schema-valid placeholder, never an operation ID to reuse.
2. Persist the requested outcome in an authoritative GitHub issue, PR, review comment, or commit before relying on an optional wake-up message.
3. Track state independently per target. Never let one target acknowledgement complete or fail another target.
4. Send the optional message with a bounded 20–30 second watchdog; the configured default is 25 seconds.
5. Acknowledgement completes transport only after the authoritative state is confirmed.
6. Explicit authoritative non-delivery may be reported as not delivered. Timeout, handler failure, or delivered-but-acknowledgement-failed yields `DELIVERY_UNKNOWN`, never `FAILED`.
7. Before retrying any side effect, reconcile the exact operation ID against the target task and authoritative GitHub state. If already applied, record success without repeating it. If confirmed absent, retry with the same operation ID. If reconciliation is unavailable or ambiguous, stop and surface `DELIVERY_UNKNOWN`.
8. Preserve the per-target operation record and provide a copy/paste fallback containing repository, issue/PR URL, operation ID, objective, expected output, evidence, and next owner. The fallback must be reconstructible from GitHub without a thread ID.

Duplicate operation IDs are idempotency keys. A target must not perform the same side effect twice.

## Correction loop

QA or Reviewer returns `changes_requested` through Coordinator. Every finding identifies an ID, evidence, expected correction, and verification. Coordinator sends the correction to the same issue-backed Implementation task. Implementation returns a finding-to-fix map with changed files or commits and exact verification. QA and Reviewer then independently re-check their findings. Implementation never approves its own correction.

## Terminal and transport states

Handoff terminal states are `completed`, `changes_requested`, and `blocked`. Transport may additionally report `DELIVERY_UNKNOWN`. Transport uncertainty does not change the underlying work state and must be reconciled before side effects are retried.
