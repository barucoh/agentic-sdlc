#!/usr/bin/env python3
import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path


PATCH_LINE = (0, 1)
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_stable_version(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value.removeprefix("v"))
    if match is None:
        raise ValueError(f"release version must be stable SemVer: {value}")
    return tuple(int(part) for part in match.groups())


def validate_release_transition(candidate: str, latest: str | None) -> None:
    candidate_version = parse_stable_version(candidate)
    if candidate_version[:2] != PATCH_LINE:
        raise ValueError("Pleiad releases must remain on the 0.1.x patch line")
    if candidate_version == (0, 1, 0):
        return
    if latest is None:
        raise ValueError("the first Pleiad release must be 0.1.0")
    latest_version = parse_stable_version(latest)
    if candidate_version == latest_version:
        return
    expected = (*PATCH_LINE, latest_version[2] + 1)
    if latest_version[:2] != PATCH_LINE or candidate_version != expected:
        expected_text = ".".join(str(part) for part in expected)
        raise ValueError(f"next Pleiad release must be the single patch increment {expected_text}")


def latest_release() -> str | None:
    request = urllib.request.Request(
        "https://api.github.com/repos/barucoh/pleiad/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "pleiad-ci"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)["tag_name"].removeprefix("v")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = (root / ".pleiad/config.yaml").read_text(encoding="utf-8")
    applied = next(line.split(":", 1)[1].strip() for line in config.splitlines() if line.startswith("applied_plugin_version:"))
    manifest = json.loads((root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    candidate = manifest["version"]
    latest = args.latest.removeprefix("v") if args.latest else latest_release()
    try:
        validate_release_transition(candidate, latest)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if latest is not None and applied == latest:
        print(f"Using latest stable Pleiad {applied}")
    elif applied == candidate:
        latest_text = latest or "none"
        print(f"Validated release candidate {candidate}; latest published stable is {latest_text}")
    else:
        raise SystemExit(
            f"Pleiad drift: applied {applied}, manifest {candidate}, latest stable {latest or 'none'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
