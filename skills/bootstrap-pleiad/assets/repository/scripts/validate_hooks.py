# pleiad:managed hook-validator/v1
#!/usr/bin/env python3
"""Deterministically validate the repository-native hook contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS_PATH = ROOT / ".codex" / "hooks.json"
GUARD_PATH = ROOT / "scripts" / "pretool_handoff_guard.py"
EXPECTED_TOOLS = {"codex_app__create_thread", "codex_app__send_message_to_thread"}


def run_guard(payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD_PATH)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )


def main() -> int:
    try:
        hooks = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
        if set(hooks) != {"hooks"}:
            raise ValueError(".codex/hooks.json must contain only the Codex-supported top-level hooks field")
        group = hooks["hooks"]["PreToolUse"][0]
        handler = group["hooks"][0]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"invalid repository hooks contract: {exc}", file=sys.stderr)
        return 2
    if not all(tool in group.get("matcher", "") for tool in EXPECTED_TOOLS):
        print("repository hook matcher does not cover observed native task tools", file=sys.stderr)
        return 2
    if (
        handler.get("type") != "command"
        or not handler.get("commandWindows")
        or "git','rev-parse','--show-toplevel" not in handler.get("command", "")
        or "git','rev-parse','--show-toplevel" not in handler.get("commandWindows", "")
        or "PLUGIN_ROOT" in json.dumps(hooks)
    ):
        print("repository hook command must be cross-platform and resolve from the Git root", file=sys.stderr)
        return 2
    template = json.loads((ROOT / ".pleiad" / "handoff-template.json").read_text(encoding="utf-8"))
    availability = {"gpt-5.6-sol": ["Medium"], "gpt-5.6-terra": ["Low", "Medium", "High"], "gpt-5.6-luna": ["Low"], "gpt-6-astra": ["High"]}
    valid = run_guard({"tool_name": "codex_app__create_thread", "tool_input": {"prompt": "PLEIAD_HANDOFF: " + json.dumps(template), "available_routes": availability}})
    legacy = run_guard({"tool_name": "codex_app__create_thread", "tool_input": {"prompt": "ASDLC_HANDOFF: " + json.dumps(template), "available_routes": availability}})
    invalid = run_guard({"tool_name": "codex_app__send_message_to_thread", "tool_input": {"prompt": "PLEIAD_HANDOFF: {\"schema_version\": \"1.0.0\"}"}})
    if valid.returncode != 0 or valid.stdout.strip():
        print("valid native-task hook probe was not allowed", file=sys.stderr)
        return 2
    if legacy.returncode != 0 or legacy.stdout.strip():
        print("legacy Agentic SDLC hook alias was not allowed", file=sys.stderr)
        return 2
    try:
        denial = json.loads(invalid.stdout)
        reason = denial["hookSpecificOutput"]["permissionDecisionReason"]
    except (KeyError, json.JSONDecodeError) as exc:
        print(f"invalid native-task hook probe did not return a denial: {exc}", file=sys.stderr)
        return 2
    if invalid.returncode != 0 or denial["hookSpecificOutput"].get("permissionDecision") != "deny" or "PLEIAD_HANDOFF_INVALID" not in reason:
        print("invalid native-task hook probe was not denied", file=sys.stderr)
        return 2
    print("Validated repository-native task-boundary hook contract for codex_app__create_thread and codex_app__send_message_to_thread")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
