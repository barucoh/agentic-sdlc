#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []

manifest_path = ROOT / ".codex-plugin" / "plugin.json"
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception as exc:
    errors.append(f"invalid plugin manifest: {exc}")
    manifest = {}

if manifest.get("name") != "agentic-sdlc":
    errors.append("plugin name must be agentic-sdlc")
if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?", str(manifest.get("version", ""))):
    errors.append("plugin version must be SemVer")

skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
required = {"bootstrap-agentic-sdlc", "adr-context", "upgrade-agentic-sdlc"}
found = {p.parent.name for p in skills}
missing = required - found
if missing:
    errors.append(f"missing skills: {', '.join(sorted(missing))}")

for skill in skills:
    text = skill.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\nname:" not in text or "\ndescription:" not in text:
        errors.append(f"invalid skill frontmatter: {skill.relative_to(ROOT)}")
    if "[TODO" in text:
        errors.append(f"placeholder remains: {skill.relative_to(ROOT)}")

required_files = [
    ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/AGENTS.md",
    ROOT / "skills/bootstrap-agentic-sdlc/assets/repository/docs/decisions/INDEX.md",
    ROOT / "docs/installation.md",
    ROOT / "docs/upgrading.md",
]
for path in required_files:
    if not path.is_file():
        errors.append(f"missing template: {path.relative_to(ROOT)}")

config = (ROOT / ".agentic-sdlc/config.yaml").read_text(encoding="utf-8")
if "bootstrap_exception: false" in config and "applied_plugin_version: bootstrap" in config:
    errors.append("non-bootstrap configuration cannot use bootstrap version")

if errors:
    print("Validation failed:")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)
print(f"Validated agentic-sdlc {manifest['version']} with {len(skills)} skills")
