#!/usr/bin/env python3
import argparse, json, urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--latest")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
config = (root / ".agentic-sdlc/config.yaml").read_text(encoding="utf-8")
applied = next(line.split(":", 1)[1].strip() for line in config.splitlines() if line.startswith("applied_plugin_version:"))
if args.latest:
    latest = args.latest
else:
    request = urllib.request.Request(
        "https://api.github.com/repos/barucoh/agentic-sdlc/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "agentic-sdlc-ci"},
    )
    with urllib.request.urlopen(request) as response:
        latest = json.load(response)["tag_name"]
latest = latest.removeprefix("v")
if applied != latest:
    raise SystemExit(f"Agentic SDLC drift: applied {applied}, latest stable {latest}")
print(f"Using latest stable Agentic SDLC {applied}")
