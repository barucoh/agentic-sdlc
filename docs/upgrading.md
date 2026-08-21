# Upgrading

Pleiad upgrades have two independent layers. Pleiad is an Agentic SDLC; its new name and identifier replace the prior Agentic SDLC plugin identity.

1. Update or reinstall the plugin package from its marketplace, then start a new Codex session.
2. Invoke `upgrade-pleiad` inside each adopted repository to migrate repository-native artifacts.

The repository migration first reports a deterministic dry run. It updates only recognized managed files or marked managed blocks, preserves project-owned Codex configuration and create-if-missing ADR files, stops on ambiguous ownership or modified managed hashes, validates the result, and records the applied schema and plugin version only after success. Run `python <plugin-root>/scripts/manage_repository.py check --target <repository>` after apply and in drift audits.

The v1.0.0 migration recognizes an installed `agentic-sdlc` v0.3.0 managed state, verifies every recorded legacy hash before changing it, then replaces it with Pleiad paths, markers, schema IDs, and managed hashes. It removes only verified old managed Pleiad-identity paths; a stale or modified legacy path stops as a conflict. A second apply is a no-op. It also continues to recognize canonical v0.2.0 generated agent files and removes only the exact obsolete generated `.codex/config.toml` role registry. A customized legacy registry stops as a conflict; unrelated project-owned `.codex/config.toml` content is preserved.

Plugin replacement is separate from repository migration: remove `agentic-sdlc`, install `pleiad`, start a new session, and then dry-run `upgrade-pleiad`. Codex saved-project labels and local checkout paths are not repository-managed state; after merge, rename or re-add the saved project through the supported Codex UI for the new checkout path.

## v1.0.0 release plan

1. Publish the Pleiad `1.0.0` plugin package after this release PR is approved and merged by `barucoh`.
2. Replace the installed `agentic-sdlc` plugin with `pleiad`, then start a new Codex session.
3. In every adopted repository, run `upgrade-pleiad` dry-run, resolve any reported managed-state conflict without overwriting custom content, then apply and check.
4. Use the supported Codex saved-project UI to point saved projects at the renamed local checkout; no repository migration changes this host state.

Existing sessions are not renamed automatically. The canonical `#<issue number> <role code> - <issue title>` policy applies prospectively unless the user explicitly requests a rename audit; repository migrations never infer or rewrite task titles.

This repository dogfoods its own releases. A release pull request must bump both the plugin manifest and repository configuration to the same version. CI accepts that explicit release-candidate state; after the owner merges it, release automation publishes the matching stable tag. Automation never receives pull-request approval or merge authority.

For local plugin development, update the local plugin source, refresh its cache-busting build metadata with the Codex plugin creator workflow, reinstall with `codex plugin add pleiad@<marketplace-name>`, and start a new session.
