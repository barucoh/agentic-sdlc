#!/usr/bin/env python3
"""Inspect and safely apply the project-owned Pleiad model-routing policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from coordination_protocol import ROUTING_POLICY_PATH, load_routing_policy, validate_routing_policy


def canonical_policy_text(policy: object) -> str:
    return json.dumps(policy, indent=2, sort_keys=True) + "\n"


def requested_policy(path: Path) -> tuple[object | None, list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"routing policy {path} is not valid JSON: {exc}"]
    return value, validate_routing_policy(value)


def target_policy_path(target: Path) -> Path:
    return target / ".pleiad" / ROUTING_POLICY_PATH.name


def inspect_or_plan(target: Path, candidate: Path | None) -> tuple[str, int]:
    destination = target_policy_path(target)
    if candidate is None:
        policy, errors = load_routing_policy(destination)
        payload = {"path": str(destination), "source": "override" if destination.exists() else "built-in defaults", "policy": policy, "errors": errors}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return "inspect", 2 if errors else 0
    policy, errors = requested_policy(candidate)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return "invalid", 2
    desired = canonical_policy_text(policy)
    current = destination.read_text(encoding="utf-8") if destination.exists() else None
    if current == desired:
        print(f"unchanged: {destination.relative_to(target).as_posix()} (already matches validated policy)")
        return "unchanged", 0
    action = "create" if current is None else "update"
    print(f"{action}: {destination.relative_to(target).as_posix()} (validated project-owned routing policy)")
    return action, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "dry-run", "apply"))
    parser.add_argument("--target", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, help="JSON file containing the complete requested policy")
    args = parser.parse_args()
    target = args.target.resolve()
    if not target.is_dir():
        parser.error(f"target is not a directory: {target}")
    if args.mode != "inspect" and args.policy is None:
        parser.error("--policy is required for dry-run and apply")
    action, status = inspect_or_plan(target, args.policy)
    if status or args.mode != "apply" or action == "unchanged":
        return status
    policy, errors = requested_policy(args.policy)
    if errors:
        return 2
    destination = target_policy_path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(canonical_policy_text(policy), encoding="utf-8", newline="\n")
    # Re-load after writing so an invalid file can never be reported as applied.
    _, errors = load_routing_policy(destination)
    if errors:
        raise RuntimeError("post-write routing policy validation failed: " + "; ".join(errors))
    print(f"Applied validated routing policy to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
