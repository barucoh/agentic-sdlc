# Agentic SDLC repository rules

- Work from a GitHub issue in a dedicated branch and worktree.
- Name new issue-delivery sessions `#<issue number> <role code> - <issue title>` using the canonical two-character role code and a maximum full title length of 36 Unicode characters.
- Keep each pull request scoped to one issue and include verification evidence.
- Never push directly to `main`; only `barucoh` may approve and merge.
- Run `python scripts/validate.py` before publishing changes.
- Treat `.codex-plugin/plugin.json` as the release-version authority.
- Do not change the version unless the pull request is intended to produce a release.
- Until the first release exists, repository-source development is the documented bootstrap exception. Afterwards use the latest stable Agentic SDLC release.

<!-- agentic-sdlc:start -->
## Agentic SDLC

- Work from an authoritative issue in a dedicated task, branch, and worktree.
- Name new delivery sessions `#<issue number> <role code> - <issue title>` with role codes `CO`, `PD`, `AR`, `IM`, `QA`, `RV`, and `KS`; cap the full title at 36 Unicode characters by truncating only the issue title and ending it with one `…`. Reject unknown role codes.
- Use Coordinator, Product, Architecture, Implementation, QA, Reviewer, and Knowledge Steward roles through `.codex/agents/`.
- Any file-producing, durable-artifact-producing, decision-heavy, release, high-importance, or risk-bearing task must use one issue-backed user-visible task, worktree, branch, and PR.
- Ephemeral subagents are limited to bounded read-only research, discovery, documentation lookup, log analysis, and evidence gathering; they may not write files or external durable state.
- Communicate through `.agentic-sdlc/handoff.schema.json`; messages are transport only and GitHub plus committed canonical artifacts remain authoritative.
- Use `adr-context` to read `docs/decisions/INDEX.md` and load only materially relevant active ADRs.
- Corrections return to the same Implementation task with a finding-to-fix verification map. Implementation never approves its own work; QA and Reviewer remain independent.
- Keep one pull request scoped to one issue. Human merge authority remains explicit.
<!-- agentic-sdlc:end -->
