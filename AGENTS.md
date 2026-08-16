# Agentic SDLC repository rules

- Work from a GitHub issue in a dedicated branch and worktree.
- Name issue-delivery sessions `<project name> #<issue number> - <issue name>`.
- Keep each pull request scoped to one issue and include verification evidence.
- Never push directly to `main`; only `barucoh` may approve and merge.
- Run `python scripts/validate.py` before publishing changes.
- Treat `.codex-plugin/plugin.json` as the release-version authority.
- Do not change the version unless the pull request is intended to produce a release.
- Until the first release exists, repository-source development is the documented bootstrap exception. Afterwards use the latest stable Agentic SDLC release.
