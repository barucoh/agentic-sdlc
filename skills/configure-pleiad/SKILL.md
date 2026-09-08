---
name: configure-pleiad
description: Inspect or change a project's validated Pleiad model-routing policy, including exceptional GPT-6 Astra escalation.
---

# Configure Pleiad routing

Use this skill to inspect effective project routing or to make an explicitly authorized model-policy change. It does not change role authority, sandboxes, lifecycle transitions, evidence gates, or human merge authority.

Run `python scripts/configure_routing.py inspect --target <repository>` first. Built-in safe defaults apply when `.pleiad/model-routing.json` is absent; the override file is project-owned and is never changed by an upgrade.

For a requested change, prepare the complete JSON policy, then run the same deterministic path with `dry-run --policy <file>` before `apply --policy <file>`. Invalid JSON, unknown fields, roles, models, efforts, and an Astra default are rejected before writing. Do not silently replace unavailable host models: host availability remains a dispatch prerequisite.

Astra's recommended exceptional route is High effort and it is never a default. A project may permit another supported effort through its explicit policy, or set `astra_enabled: false` to disable Astra while retaining other preferences. Every Astra dispatch additionally supplies `exceptional_escalation` with `difficulty: "exceptional"` and a concrete `sol_insufficiency` explanation. A failed Sol attempt is not required.

At first bootstrap, offer one concise defaults/customize choice unless the user already supplied one. For customization, validate and apply the requested policy once after the base bootstrap apply; do not ask routine users to pick a model or re-prompt during upgrades.
