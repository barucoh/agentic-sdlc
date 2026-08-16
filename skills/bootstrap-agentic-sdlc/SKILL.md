---
name: bootstrap-agentic-sdlc
description: Bootstrap Agentic SDLC into a software repository. Use when adopting the workflow, installing repository-native role configuration, or configuring issue-driven Codex delivery for a new project.
---

# Bootstrap Agentic SDLC

1. Read every applicable `AGENTS.md` and inspect existing `.codex`, `.agents`, `.github`, and `docs/decisions` conventions.
2. Derive the project display name from explicit repository metadata; ask if ambiguous.
3. Run `python <plugin-root>/scripts/manage_repository.py dry-run --target <repository> --project-name "<project name>"`. Present its deterministic create, update, managed-block update, delete, preserve, unchanged, and conflict classifications.
4. Obtain approval before writing. Never replace an existing project-owned file wholesale.
5. After approval, run the same command with `apply`, then `check`. The tool installs managed files, merges only the marked `AGENTS.md` block, preserves create-if-missing ADR files and project-owned Codex configuration, and refuses ambiguous ownership.
6. Record managed hashes in `.agentic-sdlc/managed.json`; never edit that state by hand. The applied version is updated only after a conflict-free apply.
7. Validate that standalone `.codex/agents/*.toml` roles, structured handoffs, ADR routing, durable delivery, and read-only research rules are discoverable. Report changes, preserved files, conflicts, and rollback through version control.

Use `<project name> #<issue number> - <issue name>` for every new delivery session. Do not create a delivery session without an authoritative issue.
