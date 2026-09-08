# Installation

## Local development

Clone the repository as `plugins/pleiad` beneath a marketplace root. Add `.agents/plugins/marketplace.json` at that root:

```json
{
  "name": "pleiad-local",
  "interface": { "displayName": "Pleiad Local" },
  "plugins": [{
    "name": "pleiad",
    "source": { "source": "local", "path": "./plugins/pleiad" },
    "policy": { "installation": "AVAILABLE", "authentication": "ON_INSTALL" },
    "category": "Productivity"
  }]
}
```

For a non-default marketplace, run `codex plugin marketplace add <marketplace-root>`, then `codex plugin add pleiad@pleiad-local`. Start a new session after installation.

Installing Pleiad does not register a global task hook. Bootstrap or upgrade writes the hook only into an adopted repository at .codex/hooks.json, together with its repository-local runtime. In that repository, inspect and trust the exact project-hook hash through /hooks; Codex skips changed hooks until reviewed. See [native task-boundary hooks](pleiad/hooks.md) for covered tools and verification.

## Version-pinned Git source

A team marketplace can point at a stable tag instead of a local checkout:

```json
{
  "name": "pleiad-public",
  "interface": { "displayName": "Pleiad" },
  "plugins": [{
    "name": "pleiad",
    "source": {
      "source": "url",
      "url": "https://github.com/barucoh/pleiad.git",
      "ref": "v0.1.0"
    },
    "policy": { "installation": "AVAILABLE", "authentication": "ON_INSTALL" },
    "category": "Productivity"
  }]
}
```

Pin stable tags for reproducibility. Do not point production projects at `main`.

## Universal directory

After publication, install Pleiad from the shared Plugins Directory and begin work in a new session. Until the listing is published, use a local or Git-backed marketplace.

## Replacing Agentic SDLC v0.3.0

Pleiad has a new plugin identifier. Remove the old installation with `codex plugin remove agentic-sdlc@agentic-sdlc-public` (replace the marketplace selector if your team uses a different one), then install `pleiad` from the Pleiad marketplace and start a new session. This does not modify adopted repositories; run `upgrade-pleiad` in each one afterwards.

Codex’s saved-project label and local checkout path are host UI state, so Pleiad does not edit them. After the repository rename has merged, use Codex’s supported saved-project UI to rename or remove and re-add the project, pointing it to the new local `pleiad` checkout.
