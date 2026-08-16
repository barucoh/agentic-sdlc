---
name: upgrade-agentic-sdlc
description: Upgrade an existing repository to a newer Agentic SDLC release. Use after updating the plugin or when repository-native Agentic SDLC files report schema or version drift.
---

# Upgrade Agentic SDLC

1. Read `.agentic-sdlc/config.yaml`, applicable `AGENTS.md`, and repository conventions.
2. Compare the applied schema/version with the installed plugin version.
3. Produce a dry run. Classify files as fully managed, managed block, merge, create-if-missing, validate-only, or project-owned.
4. Stop on ambiguous ownership. Never overwrite project-owned content, existing ADRs, product docs, or source code.
5. After approval, apply idempotent migrations in version order and preserve local customization.
6. Update the recorded schema and applied plugin version only after validation succeeds.
7. Report changed files, preserved customizations, unresolved conflicts, and rollback instructions.

Existing sessions are not renamed automatically. Apply new session policy prospectively unless the user requests a one-time rename audit.
