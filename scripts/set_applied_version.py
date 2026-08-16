#!/usr/bin/env python3
import re, sys
from pathlib import Path

if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", sys.argv[1].removeprefix("v")):
    raise SystemExit("usage: set_applied_version.py <semver>")
version = sys.argv[1].removeprefix("v")
path = Path(__file__).resolve().parents[1] / ".agentic-sdlc/config.yaml"
text = path.read_text(encoding="utf-8")
text = re.sub(r"(?m)^applied_plugin_version:.*$", f"applied_plugin_version: {version}", text)
text = re.sub(r"(?m)^bootstrap_exception:.*$", "bootstrap_exception: false", text)
path.write_text(text, encoding="utf-8")
print(f"Applied Agentic SDLC {version}")
