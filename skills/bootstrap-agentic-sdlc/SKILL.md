---
name: bootstrap-agentic-sdlc
description: Bootstrap Agentic SDLC into a software repository. Use when adopting the workflow, installing repository-native role configuration, or configuring issue-driven Codex delivery for a new project.
---

# Bootstrap Agentic SDLC

1. Read every applicable `AGENTS.md` and inspect existing `.codex`, `.agents`, `.github`, and `docs/decisions` conventions.
2. Derive the project display name from explicit repository metadata; ask if ambiguous.
3. Compare `assets/repository/` with the target and present a dry run grouped as create, managed-block update, preserve, and conflict.
4. Obtain approval before writing. Never replace an existing project-owned file wholesale.
5. Install missing templates. Merge the marked Agentic SDLC block into `AGENTS.md`; preserve all content outside it.
6. Record `.agentic-sdlc/config.yaml` with schema, exact applied plugin version, project name, release channel, and session-title format.
7. Validate that roles, ADR routing, and issue workflow are discoverable. Report all preserved conflicts.

Use `<project name> #<issue number> - <issue name>` for every new delivery session. Do not create a delivery session without an authoritative issue.
