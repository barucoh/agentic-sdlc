# Upgrading

Agentic SDLC upgrades have two independent layers.

1. Update or reinstall the plugin package from its marketplace, then start a new Codex session.
2. Invoke `upgrade-agentic-sdlc` inside each adopted repository to migrate repository-native artifacts.

The repository migration first reports a dry run. It updates only fully managed files or marked managed blocks, preserves project-owned content, stops on ambiguous ownership, validates the result, and records the applied schema and plugin version only after success.

Existing sessions are not renamed automatically. New session policies apply prospectively unless the user explicitly requests a rename audit.

This repository dogfoods the latest stable release. Release automation opens an owner-merged upgrade pull request after publishing each new version; automation never bypasses the owner-only merge rule.

For local plugin development, update the local plugin source, refresh its cache-busting build metadata with the Codex plugin creator workflow, reinstall with `codex plugin add agentic-sdlc@<marketplace-name>`, and start a new session.
