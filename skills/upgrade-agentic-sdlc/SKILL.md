---
name: upgrade-agentic-sdlc
description: Upgrade an existing repository to a newer Agentic SDLC release. Use after updating the plugin or when repository-native Agentic SDLC files report schema or version drift.
---

# Upgrade Agentic SDLC

1. Read `.agentic-sdlc/config.yaml`, applicable `AGENTS.md`, and repository conventions.
2. Compare the applied schema/version with the installed plugin version.
3. Run `python <plugin-root>/scripts/manage_repository.py dry-run --target <repository> --project-name "<project name>"`. Treat its classifications as authoritative.
4. Stop on every conflict. Never overwrite project-owned content, existing ADRs, source code, or locally modified managed files whose recorded hash has drifted.
5. After approval, run the same command with `apply`, then `check`. The operation is deterministic and idempotent; recognized v0.2.0 generated agents migrate to standalone current Codex agent files and the obsolete generated role registry is removed.
6. The tool validates managed-state schema, plugin version, project identity, the complete managed path set, file hashes, and managed-block hash. It writes schema, applied plugin version, and new hashes only after a conflict-free apply. `check` must pass before completion.
7. Report changed files, deleted obsolete generated files, preserved customizations, unresolved conflicts, exact validation, and version-control rollback instructions.

Existing sessions are not renamed automatically. Apply the canonical `#<issue number> <role code> - <issue title>` policy prospectively unless the user requests a one-time rename audit. Repository migration must preserve `project_name` metadata while ensuring it is absent from `session_title_format`.
