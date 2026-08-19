# Installation

## Local development

Clone the repository as `plugins/agentic-sdlc` beneath a marketplace root. Add `.agents/plugins/marketplace.json` at that root:

```json
{
  "name": "agentic-sdlc-local",
  "interface": { "displayName": "Agentic SDLC Local" },
  "plugins": [{
    "name": "agentic-sdlc",
    "source": { "source": "local", "path": "./plugins/agentic-sdlc" },
    "policy": { "installation": "AVAILABLE", "authentication": "ON_INSTALL" },
    "category": "Productivity"
  }]
}
```

For a non-default marketplace, run `codex plugin marketplace add <marketplace-root>`, then `codex plugin add agentic-sdlc@agentic-sdlc-local`. Start a new session after installation.

Agentic SDLC bundles a synchronous native task-boundary hook at `hooks/hooks.json`. After install or upgrade, inspect and trust the exact hook hash through `/hooks`; Codex skips changed plugin hooks until reviewed. See [native task-boundary hooks](agentic-sdlc/hooks.md) for covered tools and verification.

## Version-pinned Git source

A team marketplace can point at a stable tag instead of a local checkout:

```json
{
  "name": "agentic-sdlc-public",
  "interface": { "displayName": "Agentic SDLC" },
  "plugins": [{
    "name": "agentic-sdlc",
    "source": {
      "source": "url",
      "url": "https://github.com/barucoh/agentic-sdlc.git",
      "ref": "v0.2.0"
    },
    "policy": { "installation": "AVAILABLE", "authentication": "ON_INSTALL" },
    "category": "Productivity"
  }]
}
```

Pin stable tags for reproducibility. Do not point production projects at `main`.

## Universal directory

After publication, install Agentic SDLC from the shared Plugins Directory and begin work in a new session. Until the listing is published, use a local or Git-backed marketplace.
