# Agentic SDLC

A public Codex plugin for repository-native, issue-driven software delivery.

Agentic SDLC installs reusable workflows while keeping project knowledge in the repository. It provides complete role contracts, versioned structured handoffs, resilient cross-task recovery, selective ADR discovery, deterministic upgrades, and a one issue = one delivery cell = one writable Implementation worktree/branch = one PR model; QA and Reviewer use distinct subordinate, non-authoritative tasks/workspaces.

## Install during development

See [installation](docs/installation.md) for local and version-pinned Git-backed marketplace examples. Public-directory distribution will be added after the package has been validated through the OpenAI submission process.

## Use

- Invoke `bootstrap-agentic-sdlc` in a repository that has not adopted the workflow.
- Invoke `coordinate-agentic-sdlc` to route durable role work, read-only research, corrections, or uncertain cross-task delivery.
- Invoke `adr-context` when a task may be constrained by architectural decisions.
- Invoke `upgrade-agentic-sdlc` after installing a newer plugin release.

Bootstrap and upgrade always inspect first, report a deterministic dry run, preserve project-owned content, stop on managed drift or ambiguous ownership, and require explicit approval before repository writes. Apply is idempotent and followed by a drift check.

See [upgrading](docs/upgrading.md) for the two-layer plugin and repository migration model.

See the managed [coordination policy](docs/agentic-sdlc/coordination.md) and [role contracts](docs/agentic-sdlc/role-contracts.md) for the native delivery-cell model: Coordinator supervises a dedicated Implementation, QA, and Reviewer cell while GitHub remains the durable authority.

## Development

Run:

```text
python scripts/validate.py
python -m unittest discover -s tests -v
```

Pull requests target `main`. Only the repository owner may merge. Versions are sourced from `.codex-plugin/plugin.json`; merging a previously unreleased version to `main` creates the corresponding GitHub release.

## Self-hosting

This repository dogfoods Agentic SDLC. Release pull requests update both the plugin manifest and the repository's applied version; only the owner may merge, and the matching stable release is then published automatically.
