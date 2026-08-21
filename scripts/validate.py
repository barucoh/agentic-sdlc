#!/usr/bin/env python3
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coordination_protocol import (
    ROLE_CODES,
    SESSION_TITLE_FORMAT,
    SESSION_TITLE_MAX_CHARACTERS,
    format_session_title,
    validate_handoff,
    validate_session_title_config,
)
import manage_repository

ROOT = Path(__file__).resolve().parents[1]
errors = []

manifest_path = ROOT / ".codex-plugin" / "plugin.json"
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception as exc:
    errors.append(f"invalid plugin manifest: {exc}")
    manifest = {}

if manifest.get("name") != "pleiad":
    errors.append("plugin name must be pleiad")
if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?", str(manifest.get("version", ""))):
    errors.append("plugin version must be SemVer")

skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
required = {"bootstrap-pleiad", "adr-context", "upgrade-pleiad", "coordinate-pleiad"}
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
    ROOT / "skills/bootstrap-pleiad/assets/repository/AGENTS.md",
    ROOT / "skills/bootstrap-pleiad/assets/repository/docs/decisions/INDEX.md",
    ROOT / "docs/installation.md",
    ROOT / "docs/upgrading.md",
    ROOT / "skills/bootstrap-pleiad/assets/repository/.pleiad/handoff.schema.json",
    ROOT / "skills/bootstrap-pleiad/assets/repository/.pleiad/handoff-template.json",
    ROOT / "scripts/manage_repository.py",
    ROOT / "scripts/coordination_protocol.py",
    ROOT / "scripts/pretool_handoff_guard.py",
    ROOT / "scripts/validate_handoff.py",
    ROOT / ".codex/hooks.json",
    ROOT / "scripts/qa_workspace.py",
    ROOT / "scripts/validate_hooks.py",
    ROOT / "skills/bootstrap-pleiad/assets/repository/.codex/hooks.json",
    ROOT / "skills/bootstrap-pleiad/assets/repository/scripts/pretool_handoff_guard.py",
    ROOT / "skills/bootstrap-pleiad/assets/repository/scripts/validate_hooks.py",
    ROOT / "skills/bootstrap-pleiad/assets/repository/docs/pleiad/hooks.md",
]
for path in required_files:
    if not path.is_file():
        errors.append(f"missing template: {path.relative_to(ROOT)}")
if (ROOT / "hooks").is_dir() and any((ROOT / "hooks").iterdir()):
    errors.append("plugin must not bundle an automatically discovered hooks directory")

config = (ROOT / ".pleiad/config.yaml").read_text(encoding="utf-8")
errors.extend(f"invalid self-hosted session-title config: {error}" for error in validate_session_title_config(config))
if "bootstrap_exception: false" in config and "applied_plugin_version: bootstrap" in config:
    errors.append("non-bootstrap configuration cannot use bootstrap version")
applied_version = next(
    (line.split(":", 1)[1].strip() for line in config.splitlines() if line.startswith("applied_plugin_version:")),
    None,
)
if applied_version != "bootstrap" and applied_version != manifest.get("version"):
    errors.append("self-hosted applied version must match the plugin manifest")

if "schema_version: 2" not in config:
    errors.append("self-hosted repository must use Pleiad schema 2")

expected_roles = {
    "coordinator",
    "product",
    "architecture",
    "implementation",
    "qa",
    "reviewer",
    "knowledge_steward",
}
template_root = ROOT / "skills/bootstrap-pleiad/assets/repository"
agent_root = template_root / ".codex/agents"
role_names = set()
contract_sections = (
    "Inputs:",
    "Outputs:",
    "Terminal states:",
    "Escalation conditions:",
    "Forbidden actions:",
    "Permission posture:",
    "Authority:",
    "Routing:",
)
for path in sorted(agent_root.glob("*.toml")):
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"invalid agent TOML {path.relative_to(ROOT)}: {exc}")
        continue
    role_names.add(value.get("name"))
    if not value.get("description") or not value.get("developer_instructions"):
        errors.append(f"agent lacks required current Codex fields: {path.relative_to(ROOT)}")
    if value.get("sandbox_mode") not in {"read-only", "workspace-write"}:
        errors.append(f"agent lacks explicit permission posture: {path.relative_to(ROOT)}")
    for section in contract_sections:
        if section not in value.get("developer_instructions", ""):
            errors.append(f"agent contract missing {section} in {path.relative_to(ROOT)}")
    role_name = value.get("name")
    if role_name in ROLE_CODES and f"Session role code: {ROLE_CODES[role_name]}." not in value.get("developer_instructions", ""):
        errors.append(f"agent contract has incorrect session role code in {path.relative_to(ROOT)}")
    lifecycle_requirements = {
        "coordinator": "Supervise watchdogs and reconcile GitHub",
        "implementation": "Send IMPLEMENTATION_READY directly to the bound QA and Reviewer",
        "reviewer": "Reviewer is the delivery-cell verification lead",
    }
    required_lifecycle = lifecycle_requirements.get(role_name)
    if required_lifecycle and required_lifecycle not in value.get("developer_instructions", ""):
        errors.append(f"agent contract missing lifecycle ownership rule in {path.relative_to(ROOT)}")
if role_names != expected_roles:
    errors.append(f"agent roles differ: expected {sorted(expected_roles)}, found {sorted(str(x) for x in role_names)}")
if set(ROLE_CODES) != expected_roles or len(set(ROLE_CODES.values())) != len(expected_roles):
    errors.append("session role codes must map uniquely to all seven roles")
for role_name, role_code in ROLE_CODES.items():
    title = format_session_title(5, role_code, f"Validate {role_name} session title behavior")
    if len(title) > SESSION_TITLE_MAX_CHARACTERS or not title.startswith(f"#5 {role_code} - "):
        errors.append(f"invalid canonical title behavior for {role_name}")
if SESSION_TITLE_FORMAT != "#{issue_number} {role_code} - {issue_title}":
    errors.append("canonical session-title format changed unexpectedly")
if (template_root / ".codex/config.toml").exists():
    errors.append("generated repository must not use obsolete .codex/config.toml role registry")

legacy_name = "scr" + "ibe"
for relative in subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines():
    path = ROOT / relative
    if not path.is_file():
        continue
    try:
        if legacy_name.lower() in path.read_text(encoding="utf-8").lower():
            errors.append(f"legacy role name occurs in {relative}")
    except UnicodeDecodeError:
        continue

schema_path = template_root / ".pleiad/handoff.schema.json"
template_path = template_root / ".pleiad/handoff-template.json"
try:
    handoff_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    handoff_template = json.loads(template_path.read_text(encoding="utf-8"))
    errors.extend(f"invalid handoff template: {error}" for error in validate_handoff(handoff_template, handoff_schema))
    terminal_enum = handoff_schema["properties"]["terminal_state"]["enum"]
    if terminal_enum != ["completed", "changes_requested", "blocked"]:
        errors.append("handoff terminal states must be completed, changes_requested, and blocked")
except Exception as exc:
    errors.append(f"invalid handoff schema/template: {exc}")

hook_validation = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "validate_hooks.py")],
    cwd=ROOT,
    text=True,
    capture_output=True,
    check=False,
)
if hook_validation.returncode:
    errors.append("native task-boundary hook validation failed: " + hook_validation.stderr.strip())

# The checkout's .codex directory is host-managed saved-project state.  Its
# contents are intentionally not rewritten by a release rename; fresh and
# upgraded repository state is verified by the isolated migration tests.

if errors:
    print("Validation failed:")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)
print(f"Validated pleiad {manifest['version']} with {len(skills)} skills")
