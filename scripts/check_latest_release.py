#!/usr/bin/env python3
import argparse, json, urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--latest")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
config = (root / ".pleiad/config.yaml").read_text(encoding="utf-8")
applied = next(line.split(":", 1)[1].strip() for line in config.splitlines() if line.startswith("applied_plugin_version:"))
manifest = json.loads((root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
candidate = manifest["version"]
if args.latest:
    latest = args.latest
else:
    request = urllib.request.Request(
        "https://api.github.com/repos/barucoh/pleiad/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "pleiad-ci"},
    )
    with urllib.request.urlopen(request) as response:
        latest = json.load(response)["tag_name"]
latest = latest.removeprefix("v")
if applied == latest:
    print(f"Using latest stable Pleiad {applied}")
elif applied == candidate:
    print(f"Validated release candidate {candidate}; latest published stable is {latest}")
else:
    raise SystemExit(
        f"Pleiad drift: applied {applied}, manifest {candidate}, latest stable {latest}"
    )
