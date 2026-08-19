<!-- agentic-sdlc:managed hooks/v1 -->
# Native task-boundary hook guardrails

The Agentic SDLC plugin bundles `hooks/hooks.json`; Codex discovers that default path when the plugin is enabled, so the plugin manifest deliberately has no `hooks` override. The hook is synchronous `PreToolUse` policy for the observed Codex Desktop local-function names `codex_app__create_thread` and `codex_app__send_message_to_thread`; both current tool contracts carry the caller prompt in `tool_input.prompt`.

It extracts an Agentic SDLC versioned envelope from `tool_input.metadata` (`handoff`, `envelope`, or `agentic_sdlc_handoff`) or from `tool_input.prompt` as either complete JSON, a fenced JSON object, or `ASDLC_HANDOFF: { ... }`. It runs the same `scripts/coordination_protocol.py:validate_handoff` authority used by the CLI and pre-dispatch helpers. Missing or invalid envelopes are denied before a supported native call with an `ASDLC_HANDOFF_INVALID` reason; the Coordinator’s CLI preflight remains defense in depth.

## Enablement and coverage

After installing or upgrading the plugin, open `/hooks`, inspect the hook definition, and trust its exact current hash. Codex skips changed plugin hooks until that review happens again. The hook command resolves from `PLUGIN_ROOT` and has a Windows command override.

The guard covers only the two exact names above when they use Codex’s local-function hook path. `spawn_agent` is intentionally not matched because Agentic SDLC permits it only for bounded read-only research, not durable delivery. Codex documents that specialized tool paths can opt out of hooks; such a path is not an enforcement claim. If the supported Desktop host reports different `tool_name` values or bypasses these calls, stop the delivery as blocked, record the observed payload, and update the matcher only after review. Hooks are local guardrails, not a durable transport authority; GitHub and committed artifacts remain authoritative.

## Host verification procedure

Use `python <plugin-root>/scripts/validate_hooks.py` for the deterministic contract probe. Then, in a newly started Desktop session with the plugin hook trusted, invoke an invalid task creation/send envelope and verify the call is denied before side effects; invoke a valid envelope and verify the native call crosses the boundary. Record the exact tool names and result in the issue/PR. A running session cannot claim this final probe for a newly changed, untrusted hook.
