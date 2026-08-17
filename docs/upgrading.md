# Upgrading

Agentic SDLC upgrades have two independent layers.

1. Update or reinstall the plugin package from its marketplace, then start a new Codex session.
2. Invoke `upgrade-agentic-sdlc` inside each adopted repository to migrate repository-native artifacts.

The repository migration first reports a deterministic dry run. It updates only recognized managed files or marked managed blocks, preserves project-owned Codex configuration and create-if-missing ADR files, stops on ambiguous ownership or modified managed hashes, validates the result, and records the applied schema and plugin version only after success. Run `python <plugin-root>/scripts/manage_repository.py check --target <repository>` after apply and in drift audits.

The v0.3.0 migration recognizes canonical v0.2.0 generated agent files, converts them to current standalone `.codex/agents/*.toml` contracts, and removes only the exact obsolete generated `.codex/config.toml` role registry. A customized legacy registry stops as a conflict; unrelated project-owned `.codex/config.toml` content is preserved.

Existing sessions are not renamed automatically. The canonical `#<issue number> <role code> - <issue title>` policy applies prospectively unless the user explicitly requests a rename audit; repository migrations never infer or rewrite task titles.

This repository dogfoods its own releases. A release pull request must bump both the plugin manifest and repository configuration to the same version. CI accepts that explicit release-candidate state; after the owner merges it, release automation publishes the matching stable tag. Automation never receives pull-request approval or merge authority.

For local plugin development, update the local plugin source, refresh its cache-busting build metadata with the Codex plugin creator workflow, reinstall with `codex plugin add agentic-sdlc@<marketplace-name>`, and start a new session.
