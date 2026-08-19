#!/usr/bin/env python3
"""Synchronous native-task guard for Agentic SDLC plugin hooks."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


TOOL_NAMES = frozenset({"codex_app__create_thread", "codex_app__send_message_to_thread"})
SCHEMA_VERSION = "1.0.0"


def plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT")
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


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
    marker = re.search(r"ASDLC_HANDOFF\s*:\s*(\{.*)", text, re.DOTALL)
    if marker:
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(marker.group(1))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    return _json_object(fenced.group(1)) if fenced else None


def extract_envelope(tool_input: Any) -> dict[str, Any] | None:
    if not isinstance(tool_input, dict):
        return None
    for key in ("handoff", "envelope", "agentic_sdlc_handoff"):
        candidate = tool_input.get(key)
        if isinstance(candidate, dict):
            return candidate
    metadata = tool_input.get("metadata")
    if isinstance(metadata, dict):
        for key in ("handoff", "envelope", "agentic_sdlc_handoff"):
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
                "ASDLC_HANDOFF_INVALID\n"
                f"- missing/invalid fields: {concise}\n"
                f"Rebuild with schema_version {SCHEMA_VERSION} and retry; no task/message was created."
            ),
        }
    }


def evaluate(event: dict[str, Any], root: Path | None = None) -> dict[str, Any] | None:
    if event.get("tool_name") not in TOOL_NAMES:
        return None
    envelope = extract_envelope(event.get("tool_input"))
    if envelope is None:
        return denial(["missing versioned ASDLC envelope in tool_input prompt or metadata"])
    errors = canonical_validator(root or plugin_root())(envelope)
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
