<!-- pleiad:managed hooks/v2 -->
# Native task-boundary hook guardrails

Pleiad does not install a plugin-global hook. Bootstrap and upgrade manage .codex/hooks.json, scripts/pretool_handoff_guard.py, and scripts/validate_hooks.py only inside an adopted Pleiad repository. This prevents an enabled plugin from applying Pleiad policy to unrelated projects.

The project hook is synchronous PreToolUse policy for the observed Codex Desktop local-function names codex_app__create_thread and codex_app__send_message_to_thread; both current tool contracts carry the caller prompt in tool_input.prompt. Its command resolves the Git root with git rev-parse --show-toplevel and then runs the repository-local guard. It never uses PLUGIN_ROOT.

The guard extracts an Pleiad versioned envelope from tool_input.metadata (handoff, envelope, or pleiad_handoff) or from tool_input.prompt as complete JSON, a fenced JSON object, or ASDLC_HANDOFF: { ... }. It runs the same scripts/coordination_protocol.py:validate_handoff authority used by the CLI and pre-dispatch helpers. Missing or invalid envelopes are denied before a supported native call with an ASDLC_HANDOFF_INVALID reason; the Coordinator’s CLI preflight remains defense in depth.

## Enablement and coverage

After bootstrap or upgrade, open /hooks from the adopted repository, inspect the project hook definition, and trust its exact current hash. Codex skips changed non-managed command hooks until that review happens again. The managed configuration supplies POSIX and Windows commands, both resolving the guard from the Git root.

The guard covers only the two exact names above when they use Codex’s local-function hook path. spawn_agent is intentionally not matched because Pleiad permits it only for bounded read-only research, not durable delivery. Codex documents that specialized tool paths can opt out of hooks; such a path is not an enforcement claim. If the supported Desktop host reports different tool_name values or bypasses these calls, stop the delivery as blocked, record the observed payload, and update the matcher only after review. Hooks are local guardrails, not a durable transport authority; GitHub and committed artifacts remain authoritative.

## Host verification procedure

Run python scripts/validate_hooks.py from the adopted repository for the deterministic contract probe. Then, in a newly started Desktop session with the project hook trusted, invoke an invalid task creation/send envelope and verify the call is denied before side effects; invoke a valid envelope and verify the native call crosses the boundary. Record the exact tool names and result in the issue/PR. A running session cannot claim this final probe for a newly changed, untrusted hook.
