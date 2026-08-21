# pleiad:managed hook-guard/v1
#!/usr/bin/env python3
"""Synchronous repository-native task-boundary guard for Pleiad."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


TOOL_NAMES = frozenset({"codex_app__create_thread", "codex_app__send_message_to_thread"})
SCHEMA_VERSION = "1.0.0"


def repository_root() -> Path:
    """Resolve the hook authority from the current Git repository, never a plugin."""

    output = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    return Path(output.strip()).resolve()


def is_pleiad_project(root: Path) -> bool:
    """A copied command cannot impose Pleiad policy on an unrelated repository."""

    return all(
        (root / relative).is_file()
        for relative in (
            Path(".pleiad/config.yaml"),
            Path(".pleiad/handoff.schema.json"),
            Path("scripts/coordination_protocol.py"),
        )
    )


def canonical_validator(root: Path):
    sys.path.insert(0, str(root / "scripts"))
    from coordination_protocol import validate_handoff  # noqa: PLC0415

    return validate_handoff


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _envelope_from_text(text: str) -> dict[str, Any] | None:
    direct = _json_object(text.strip())
    if direct is not None:
        return direct
    marker = re.search(r"(?:PLEIAD_HANDOFF|ASDLC_HANDOFF)\s*:\s*(\{.*)", text, re.DOTALL)
    if marker:
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(marker.group(1))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    fenced = re.search(r"\x60\x60\x60(?:json)?\s*(\{.*?\})\s*\x60\x60\x60", text, re.DOTALL | re.IGNORECASE)
    return _json_object(fenced.group(1)) if fenced else None


def extract_envelope(tool_input: Any) -> dict[str, Any] | None:
    if not isinstance(tool_input, dict):
        return None
    for key in ("handoff", "envelope", "pleiad_handoff", "agentic_sdlc_handoff"):
        candidate = tool_input.get(key)
        if isinstance(candidate, dict):
            return candidate
    metadata = tool_input.get("metadata")
    if isinstance(metadata, dict):
        for key in ("handoff", "envelope", "pleiad_handoff", "agentic_sdlc_handoff"):
            candidate = metadata.get(key)
            if isinstance(candidate, dict):
                return candidate
    prompt = tool_input.get("prompt")
    return _envelope_from_text(prompt) if isinstance(prompt, str) else None


def denial(errors: list[str]) -> dict[str, Any]:
    concise = "; ".join(errors[:3]) or "missing versioned handoff envelope"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "PLEIAD_HANDOFF_INVALID\n"
                f"- missing/invalid fields: {concise}\n"
                f"Rebuild with schema_version {SCHEMA_VERSION} and retry; no task/message was created."
            ),
        }
    }


def evaluate(event: dict[str, Any], root: Path | None = None) -> dict[str, Any] | None:
    if event.get("tool_name") not in TOOL_NAMES:
        return None
    try:
        resolved_root = (root or repository_root()).resolve()
    except (OSError, subprocess.CalledProcessError):
        return None
    if not is_pleiad_project(resolved_root):
        return None
    envelope = extract_envelope(event.get("tool_input"))
    if envelope is None:
        return denial(["missing versioned Pleiad envelope in tool_input prompt or metadata"])
    errors = canonical_validator(resolved_root)(envelope)
    return denial(errors) if errors else None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps(denial([f"invalid hook input: {exc.msg}"])))
        return 0
    result = evaluate(event)
    if result is not None:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
