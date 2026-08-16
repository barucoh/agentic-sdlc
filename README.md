# Agentic SDLC

A public Codex plugin for repository-native, issue-driven software delivery.

Agentic SDLC installs reusable workflows while keeping project knowledge in the repository. It provides specialized roles, structured handoffs, selective ADR discovery, safe upgrades, and a one-issue/one-task/one-worktree delivery model.

## Install during development

Clone this repository, add it as a local or Git-backed plugin marketplace source, install `agentic-sdlc`, and start a new Codex session. Public-directory distribution will be added after the package has been validated through the OpenAI submission process.

## Use

- Invoke `bootstrap-agentic-sdlc` in a repository that has not adopted the workflow.
- Invoke `adr-context` when a task may be constrained by architectural decisions.
- Invoke `upgrade-agentic-sdlc` after installing a newer plugin release.

Bootstrap and upgrade always inspect first, report a dry run, preserve local customizations, and require explicit approval before repository writes.

## Development

Run:

```text
python scripts/validate.py
```

Pull requests target `main`. Only the repository owner may merge. Versions are sourced from `.codex-plugin/plugin.json`; merging a previously unreleased version to `main` creates the corresponding GitHub release.

## Self-hosting

The initial release uses the repository source as a bootstrap exception. After `v0.1.0` is published, this repository pins and uses its latest stable release. Release `N` is developed with the latest prior stable release, then an owner-merged upgrade PR moves the repository to `N`.
