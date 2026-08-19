#!/usr/bin/env python3
"""Deterministically validate the plugin native task-boundary hook contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS_PATH = ROOT / "hooks" / "hooks.json"
GUARD_PATH = ROOT / "hooks" / "pretool_handoff_guard.py"
EXPECTED_TOOLS = {"codex_app__create_thread", "codex_app__send_message_to_thread"}


def run_guard(payload: dict) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PLUGIN_ROOT": str(ROOT)}
    return subprocess.run(
        [sys.executable, str(GUARD_PATH)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def main() -> int:
    try:
        hooks = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
        if set(hooks) != {"hooks"}:
            raise ValueError("hooks/hooks.json must contain only the Codex-supported top-level hooks field")
        group = hooks["hooks"]["PreToolUse"][0]
        handler = group["hooks"][0]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"invalid plugin hooks contract: {exc}", file=sys.stderr)
        return 2
    if not all(tool in group.get("matcher", "") for tool in EXPECTED_TOOLS):
        print("plugin hook matcher does not cover observed native task tools", file=sys.stderr)
        return 2
    if handler.get("type") != "command" or "PLUGIN_ROOT" not in handler.get("command", "") or not handler.get("commandWindows"):
        print("plugin hook command must be cross-platform and resolve through PLUGIN_ROOT", file=sys.stderr)
        return 2
    template = json.loads((ROOT / ".agentic-sdlc" / "handoff-template.json").read_text(encoding="utf-8"))
    valid = run_guard({"tool_name": "codex_app__create_thread", "tool_input": {"prompt": "ASDLC_HANDOFF: " + json.dumps(template)}})
    invalid = run_guard({"tool_name": "codex_app__send_message_to_thread", "tool_input": {"prompt": "ASDLC_HANDOFF: {\"schema_version\": \"1.0.0\"}"}})
    if valid.returncode != 0 or valid.stdout.strip():
        print("valid native-task hook probe was not allowed", file=sys.stderr)
        return 2
    try:
        denial = json.loads(invalid.stdout)
        reason = denial["hookSpecificOutput"]["permissionDecisionReason"]
    except (KeyError, json.JSONDecodeError) as exc:
        print(f"invalid native-task hook probe did not return a denial: {exc}", file=sys.stderr)
        return 2
    if invalid.returncode != 0 or denial["hookSpecificOutput"].get("permissionDecision") != "deny" or "ASDLC_HANDOFF_INVALID" not in reason:
        print("invalid native-task hook probe was not denied", file=sys.stderr)
        return 2
    print("Validated native task-boundary hook contract for codex_app__create_thread and codex_app__send_message_to_thread")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
