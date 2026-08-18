# agentic-sdlc:managed runtime/v1
#!/usr/bin/env python3
"""Validate one versioned Agentic SDLC handoff before dispatch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from coordination_protocol import validate_handoff  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a structured handoff envelope")
    parser.add_argument("handoff", nargs="?", help="JSON file; stdin when omitted")
    args = parser.parse_args(argv)
    try:
        raw = Path(args.handoff).read_text(encoding="utf-8") if args.handoff else sys.stdin.read()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid handoff input: {exc}", file=sys.stderr)
        return 2
    errors = validate_handoff(value)
    if errors:
        print("invalid handoff:")
        for error in errors:
            print(f"- {error}")
        return 2
    print("valid handoff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
