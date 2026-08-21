#!/usr/bin/env python3
"""Deterministically bootstrap, upgrade, or check repository-native Pleiad state."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from coordination_protocol import (
    ROLE_CODES,
    format_session_title,
    validate_handoff,
    validate_session_title_config,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "skills" / "bootstrap-pleiad" / "assets" / "repository"
MANIFEST_PATH = Path(".pleiad/managed.json")
LEGACY_MANIFEST_PATH = Path(".agentic-sdlc/managed.json")
AGENTS_PATH = Path("AGENTS.md")
BLOCK_START = "<!-- pleiad:start -->"
BLOCK_END = "<!-- pleiad:end -->"
LEGACY_BLOCK_START = "<!-- agentic-sdlc:start -->"
LEGACY_BLOCK_END = "<!-- agentic-sdlc:end -->"
MANIFEST_SCHEMA_VERSION = 2
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

MANAGED_PATHS = tuple(
    Path(path)
    for path in (
        ".pleiad/config.yaml",
        ".pleiad/handoff.schema.json",
        ".pleiad/handoff-template.json",
        ".codex/hooks.json",
        ".codex/agents/architecture.toml",
        ".codex/agents/coordinator.toml",
        ".codex/agents/implementation.toml",
        ".codex/agents/knowledge_steward.toml",
        ".codex/agents/product.toml",
        ".codex/agents/qa.toml",
        ".codex/agents/reviewer.toml",
        "scripts/coordination_protocol.py",
        "scripts/pretool_handoff_guard.py",
        "scripts/validate_handoff.py",
        "scripts/validate_hooks.py",
        "scripts/qa_workspace.py",
        "docs/pleiad/coordination.md",
        "docs/pleiad/hooks.md",
        "docs/pleiad/role-contracts.md",
    )
)
MANIFEST_V1_MANAGED_PATHS = frozenset(
    frozenset(MANAGED_PATHS)
    - {
        Path(".codex/hooks.json"),
        Path("scripts/pretool_handoff_guard.py"),
        Path("scripts/validate_hooks.py"),
    }
)
CREATE_IF_MISSING = (
    Path("docs/decisions/INDEX.md"),
    Path("docs/decisions/ADR-TEMPLATE.md"),
)
OBSOLETE_REGISTRY = Path(".codex/config.toml")
LEGACY_HASHES = {
    Path(".codex/config.toml"): "6603ea8df47e0781dabe7315cc09a43c490acbbfa7b4cd906d3b045f1e01f737",
    Path(".codex/agents/coordinator.toml"): "7a7efde0a6ccf1856f8ca36511e133d68eb749d66af632810812965d65366e43",
    Path(".codex/agents/product.toml"): "46c536f634740723b3992312dda9995da562036ac78b8ea4d85bdb2882770ae1",
    Path(".codex/agents/architecture.toml"): "878856ccf0c1010e95e835cf668b0b234ae16812617f450e90615fd6d4972aaf",
    Path(".codex/agents/implementation.toml"): "0e097f07f59e8a96a339a0d026431ebb3711a1cc459ee2403cbe744a35c756bd",
    Path(".codex/agents/qa.toml"): "dcaaaace48d508e82d20c54d82380e59dc42d208d24c859deef0b644334f0d48",
    Path(".codex/agents/reviewer.toml"): "2037f71b7283b30ed57ddf6bd12d13433bcd538cb7098f93b40daff70ee55d7e",
    Path(".codex/agents/knowledge_steward.toml"): "06337a7c086c0c272f0a9689cb8c7bc37c7b17f3848b859fcdf79b442cb3c308",
}


@dataclass(frozen=True)
class Action:
    classification: str
    path: Path
    reason: str


def digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def digest_path(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def plugin_version() -> str:
    manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    return manifest["version"]


def template_text(relative: Path, project_name: str) -> str:
    text = (TEMPLATES / relative).read_text(encoding="utf-8")
    if relative == Path(".pleiad/config.yaml"):
        text = re.sub(
            r"(?m)^project_name: .+$",
            f"project_name: {json.dumps(project_name, ensure_ascii=False)}",
            text,
        )
        text = re.sub(
            r"(?m)^applied_plugin_version: .+$",
            f"applied_plugin_version: {plugin_version()}",
            text,
        )
    return text


def load_installed_manifest(target: Path) -> dict:
    return load_manifest(target / MANIFEST_PATH)


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"invalid": True}
    return value if isinstance(value, dict) else {"invalid": True}


def legacy_path(relative: Path) -> Path:
    return Path(str(relative).replace(".pleiad/", ".agentic-sdlc/").replace("docs/pleiad/", "docs/agentic-sdlc/"))


def legacy_manifest_errors(target: Path, installed: dict) -> list[str]:
    """Accept only a pristine Agentic SDLC v0.3.0 installation for migration."""
    errors: list[str] = []
    if installed.get("invalid"):
        return ["legacy Agentic SDLC managed-state manifest is not a valid JSON object"]
    if installed.get("schema_version") != 2 or installed.get("plugin_version") != "0.3.0":
        errors.append("legacy managed-state must be Agentic SDLC v0.3.0 schema 2")
    files = installed.get("files")
    expected = {legacy_path(path).as_posix() for path in MANAGED_PATHS}
    if not isinstance(files, dict) or set(files) != expected:
        errors.append("legacy managed-state files do not match the v0.3.0 managed path set")
    elif any(not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None for value in files.values()):
        errors.append("legacy managed-state contains an invalid file hash")
    else:
        for path_text, recorded in files.items():
            path = target / path_text
            if not path.is_file() or digest_path(path) != recorded:
                errors.append(f"legacy managed-state hash does not match {path_text}")
    blocks = installed.get("managed_blocks")
    recorded_block = blocks.get(AGENTS_PATH.as_posix()) if isinstance(blocks, dict) else None
    block = extract_agents_block(target.joinpath(AGENTS_PATH).read_text(encoding="utf-8"), LEGACY_BLOCK_START, LEGACY_BLOCK_END) if (target / AGENTS_PATH).is_file() else None
    if not isinstance(recorded_block, str) or block is None or digest_bytes(block.encode("utf-8")) != recorded_block:
        errors.append("legacy managed-state AGENTS.md block hash does not match")
    return errors


def extract_agents_block(text: str, start_marker: str = BLOCK_START, end_marker: str = BLOCK_END) -> str | None:
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        return None
    start = text.index(start_marker)
    end = text.index(end_marker, start) + len(end_marker)
    return text[start:end]


def desired_manifest_value(project_name: str) -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "plugin_version": plugin_version(),
        "project_name": project_name,
        "files": {
            relative.as_posix(): digest_bytes(template_text(relative, project_name).encode("utf-8"))
            for relative in MANAGED_PATHS
        },
        "managed_blocks": {
            AGENTS_PATH.as_posix(): digest_bytes(desired_agents_text().encode("utf-8")),
        },
    }


def desired_manifest_text(project_name: str) -> str:
    return json.dumps(desired_manifest_value(project_name), indent=2, sort_keys=True) + "\n"


def manifest_validation_errors(target: Path, installed: dict, project_name: str) -> list[str]:
    if not (target / MANIFEST_PATH).exists():
        return []
    if installed.get("invalid"):
        return ["managed-state manifest is not a valid JSON object"]

    errors: list[str] = []
    expected_keys = {"schema_version", "plugin_version", "project_name", "files", "managed_blocks"}
    if set(installed) != expected_keys:
        errors.append("managed-state manifest fields differ from the authority schema")
    is_v1 = installed.get("schema_version") == 1
    if installed.get("schema_version") not in {1, MANIFEST_SCHEMA_VERSION}:
        errors.append(f"managed-state schema_version must be {MANIFEST_SCHEMA_VERSION}")
    if installed.get("plugin_version") != plugin_version():
        errors.append(f"managed-state plugin_version must be {plugin_version()}")
    if installed.get("project_name") != project_name:
        errors.append(f"managed-state project_name must be {project_name!r}")

    files = installed.get("files")
    expected_paths = {relative.as_posix() for relative in MANAGED_PATHS}
    recorded_paths = {relative.as_posix() for relative in (MANIFEST_V1_MANAGED_PATHS if is_v1 else MANAGED_PATHS)}
    if not isinstance(files, dict):
        errors.append("managed-state files must be an object")
    else:
        if set(files) != recorded_paths:
            errors.append("managed-state files must list every and only managed path")
        for relative_text in sorted(recorded_paths):
            recorded = files.get(relative_text)
            if not isinstance(recorded, str) or SHA256_PATTERN.fullmatch(recorded) is None:
                errors.append(f"managed-state hash is invalid for {relative_text}")
                continue
            destination = target / Path(relative_text)
            if not destination.is_file():
                errors.append(f"managed-state path is missing: {relative_text}")
            elif digest_path(destination) != recorded:
                errors.append(f"managed-state hash does not match {relative_text}")

    blocks = installed.get("managed_blocks")
    if not isinstance(blocks, dict) or set(blocks) != {AGENTS_PATH.as_posix()}:
        errors.append("managed-state managed_blocks must contain only AGENTS.md")
    else:
        recorded_block = blocks[AGENTS_PATH.as_posix()]
        if not isinstance(recorded_block, str) or SHA256_PATTERN.fullmatch(recorded_block) is None:
            errors.append("managed-state AGENTS.md block hash is invalid")
        agents_path = target / AGENTS_PATH
        actual_block = extract_agents_block(agents_path.read_text(encoding="utf-8")) if agents_path.is_file() else None
        if actual_block is None:
            errors.append("managed-state AGENTS.md block is missing or malformed")
        elif digest_bytes(actual_block.encode("utf-8")) != recorded_block:
            errors.append("managed-state AGENTS.md block hash does not match")
    return errors


def recorded_file_hash(installed: dict, relative: Path) -> str | None:
    files = installed.get("files")
    if not isinstance(files, dict):
        return None
    recorded = files.get(relative.as_posix())
    return recorded if isinstance(recorded, str) else None


def recognized_legacy_config(text: str) -> bool:
    keys = {
        line.split(":", 1)[0]
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and ":" in line
    }
    return keys == {
        "schema_version",
        "project_name",
        "applied_plugin_version",
        "release_channel",
        "session_title_format",
        "bootstrap_exception",
    } and re.search(r"(?m)^schema_version: 1$", text) is not None


def configured_project_name(target: Path) -> str | None:
    path = target / ".pleiad/config.yaml"
    if not path.exists():
        return None
    match = re.search(r"(?m)^project_name:\s*(.+)$", path.read_text(encoding="utf-8"))
    if not match:
        return None
    raw = match.group(1).strip()
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
            return value if isinstance(value, str) and value else None
        except json.JSONDecodeError:
            return None
    return raw or None


def configured_legacy_project_name(target: Path) -> str | None:
    """Read, but do not yet trust, the legacy name until plan validation."""
    legacy = load_manifest(target / LEGACY_MANIFEST_PATH)
    name = legacy.get("project_name")
    return name if isinstance(name, str) and name else None


def template_validation_errors(project_name: str) -> list[str]:
    errors: list[str] = []
    for relative in MANAGED_PATHS:
        content = template_text(relative, project_name)
        try:
            if relative.suffix == ".toml":
                value = tomllib.loads(content)
                for field in ("name", "description", "developer_instructions", "sandbox_mode"):
                    if not value.get(field):
                        errors.append(f"{relative.as_posix()} lacks {field}")
            elif relative.suffix == ".json":
                json.loads(content)
        except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{relative.as_posix()} is invalid: {exc}")
    block = desired_agents_text()
    if block.count(BLOCK_START) != 1 or block.count(BLOCK_END) != 1:
        errors.append("AGENTS.md template must contain exactly one managed block")
    config = template_text(Path(".pleiad/config.yaml"), project_name)
    errors.extend(f"config: {error}" for error in validate_session_title_config(config))
    for role, code in ROLE_CODES.items():
        try:
            format_session_title(5, code, f"Validate {role} title")
        except ValueError as exc:
            errors.append(f"config: invalid role code {role}={code}: {exc}")
    try:
        schema = json.loads(template_text(Path(".pleiad/handoff.schema.json"), project_name))
        handoff = json.loads(template_text(Path(".pleiad/handoff-template.json"), project_name))
        errors.extend(f"handoff template: {error}" for error in validate_handoff(handoff, schema))
    except json.JSONDecodeError as exc:
        errors.append(f"handoff schema or template is invalid JSON: {exc}")
    return errors


def is_safe_managed_update(target: Path, relative: Path, installed: dict, legacy: dict | None = None) -> bool:
    path = target / relative
    recorded = recorded_file_hash(installed, relative)
    if recorded:
        return recorded == digest_path(path)
    if legacy:
        legacy_recorded = legacy.get("files", {}).get(legacy_path(relative).as_posix())
        if isinstance(legacy_recorded, str):
            return legacy_recorded == digest_path(path)
    if LEGACY_HASHES.get(relative) == digest_path(path):
        return True
    if relative == Path(".pleiad/config.yaml"):
        return recognized_legacy_config(path.read_text(encoding="utf-8"))
    first_line = path.read_text(encoding="utf-8").splitlines()[0] if path.stat().st_size else ""
    return first_line.startswith("# pleiad:managed") or first_line.startswith("<!-- pleiad:managed")


def desired_agents_text() -> str:
    source = (TEMPLATES / AGENTS_PATH).read_text(encoding="utf-8")
    start = source.index(BLOCK_START)
    end = source.index(BLOCK_END, start) + len(BLOCK_END)
    return source[start:end]


def merge_agents(existing: str, block: str) -> tuple[str | None, str]:
    if existing.count(LEGACY_BLOCK_START) == 1 and existing.count(LEGACY_BLOCK_END) == 1:
        existing = existing.replace(LEGACY_BLOCK_START, BLOCK_START).replace(LEGACY_BLOCK_END, BLOCK_END)
    starts = existing.count(BLOCK_START)
    ends = existing.count(BLOCK_END)
    if starts != ends or starts > 1:
        return None, "malformed or duplicate managed block"
    if starts == 1:
        start = existing.index(BLOCK_START)
        end = existing.index(BLOCK_END, start) + len(BLOCK_END)
        merged = existing[:start] + block + existing[end:]
        return merged, "managed block update"
    separator = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + separator + block + "\n", "managed block append"


def plan(target: Path, project_name: str) -> tuple[list[Action], dict[Path, str], list[str]]:
    installed = load_installed_manifest(target)
    legacy: dict | None = None
    actions: list[Action] = []
    writes: dict[Path, str] = {}
    delete_paths: list[str] = []
    legacy_manifest = target / LEGACY_MANIFEST_PATH
    if legacy_manifest.exists():
        if (target / MANIFEST_PATH).exists():
            actions.append(Action("conflict", LEGACY_MANIFEST_PATH, "both Pleiad and legacy managed state exist"))
        else:
            legacy = load_manifest(legacy_manifest)
            for error in legacy_manifest_errors(target, legacy):
                actions.append(Action("conflict", LEGACY_MANIFEST_PATH, error))
            if legacy.get("project_name") != project_name:
                actions.append(Action("conflict", LEGACY_MANIFEST_PATH, "legacy project_name conflicts with the requested project name"))
            if not [action for action in actions if action.classification == "conflict"]:
                for path in MANAGED_PATHS:
                    old_path = legacy_path(path)
                    if old_path != path:
                        actions.append(Action("delete", old_path, "verified Agentic SDLC v0.3.0 managed path"))
                        delete_paths.append(old_path.as_posix())
                actions.append(Action("delete", LEGACY_MANIFEST_PATH, "verified Agentic SDLC v0.3.0 managed state"))
                delete_paths.append(LEGACY_MANIFEST_PATH.as_posix())
    for error in template_validation_errors(project_name):
        actions.append(Action("conflict", Path(".pleiad"), f"invalid release template: {error}"))
    manifest_path = target / MANIFEST_PATH
    manifest_errors = manifest_validation_errors(target, installed, project_name)
    for error in manifest_errors:
        actions.append(Action("conflict", MANIFEST_PATH, error))
    if not manifest_path.exists():
        actions.append(Action("create", MANIFEST_PATH, "missing managed-state authority file"))
    elif not manifest_errors and manifest_path.read_text(encoding="utf-8") != desired_manifest_text(project_name):
        actions.append(Action("update", MANIFEST_PATH, "managed-state metadata or desired hashes changed"))

    for relative in MANAGED_PATHS:
        desired = template_text(relative, project_name)
        destination = target / relative
        if not destination.exists():
            actions.append(Action("create", relative, "missing managed file"))
            writes[relative] = desired
        elif destination.read_text(encoding="utf-8") == desired:
            actions.append(Action("unchanged", relative, "matches release candidate"))
        elif is_safe_managed_update(target, relative, installed, legacy):
            actions.append(Action("update", relative, "recognized managed or v0.2.0 file"))
            writes[relative] = desired
        else:
            actions.append(Action("conflict", relative, "local content is not recognized as managed"))

    agents_path = target / AGENTS_PATH
    existing_agents = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    merged_agents, agents_reason = merge_agents(existing_agents, desired_agents_text())
    if merged_agents is None:
        actions.append(Action("conflict", AGENTS_PATH, agents_reason))
    elif not agents_path.exists():
        actions.append(Action("create", AGENTS_PATH, agents_reason))
        writes[AGENTS_PATH] = merged_agents
    elif merged_agents != existing_agents:
        actions.append(Action("managed-block-update", AGENTS_PATH, agents_reason))
        writes[AGENTS_PATH] = merged_agents
    else:
        actions.append(Action("unchanged", AGENTS_PATH, "managed block matches"))

    for relative in CREATE_IF_MISSING:
        destination = target / relative
        if destination.exists():
            actions.append(Action("preserve", relative, "project-owned create-if-missing file exists"))
        else:
            actions.append(Action("create", relative, "missing create-if-missing file"))
            writes[relative] = template_text(relative, project_name)

    obsolete = target / OBSOLETE_REGISTRY
    delete_obsolete: str | None = None
    if obsolete.exists():
        obsolete_hash = digest_path(obsolete)
        recorded = recorded_file_hash(installed, OBSOLETE_REGISTRY)
        text = obsolete.read_text(encoding="utf-8")
        if obsolete_hash == LEGACY_HASHES[OBSOLETE_REGISTRY] or recorded == obsolete_hash:
            actions.append(Action("delete", OBSOLETE_REGISTRY, "obsolete v0.2.0 role registry"))
            delete_obsolete = OBSOLETE_REGISTRY.as_posix()
        elif "config_file = \"agents/" in text:
            actions.append(Action("conflict", OBSOLETE_REGISTRY, "customized legacy role registry requires manual migration"))
        else:
            actions.append(Action("preserve", OBSOLETE_REGISTRY, "project-owned Codex configuration"))

    actions.sort(key=lambda action: (action.path.as_posix(), action.classification))
    if delete_obsolete:
        delete_paths.append(delete_obsolete)
    return actions, writes, delete_paths


def apply(target: Path, writes: dict[Path, str], delete_paths: list[str], project_name: str) -> None:
    config_path = Path(".pleiad/config.yaml")
    config_content = writes.get(config_path)
    staged_writes = {path: content for path, content in writes.items() if path != config_path}
    for relative, content in sorted(staged_writes.items(), key=lambda item: item[0].as_posix()):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")
    for delete_path in delete_paths:
        path = target / delete_path
        if path.exists():
            path.unlink()
    # Remove legacy identity directories only when migration left them empty;
    # any unrecognized project content remains untouched.
    for directory in (target / ".agentic-sdlc", target / "docs/agentic-sdlc"):
        try:
            directory.rmdir()
        except OSError:
            pass
    for relative, content in staged_writes.items():
        if (target / relative).read_text(encoding="utf-8") != content:
            raise RuntimeError(f"post-write validation failed: {relative.as_posix()}")
    if config_content is not None:
        destination = target / config_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(config_content, encoding="utf-8", newline="\n")
        if destination.read_text(encoding="utf-8") != config_content:
            raise RuntimeError("post-write validation failed: .pleiad/config.yaml")
    expected_manifest = desired_manifest_value(project_name)
    for relative_text, expected_hash in expected_manifest["files"].items():
        if digest_path(target / relative_text) != expected_hash:
            raise RuntimeError(f"managed-state validation failed: {relative_text}")
    actual_block = extract_agents_block((target / AGENTS_PATH).read_text(encoding="utf-8"))
    if actual_block is None or digest_bytes(actual_block.encode("utf-8")) != expected_manifest["managed_blocks"][AGENTS_PATH.as_posix()]:
        raise RuntimeError("managed-state validation failed: AGENTS.md block")
    manifest = target / MANIFEST_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    desired_manifest = desired_manifest_text(project_name).encode("utf-8")
    # Managed hashes and plan comparisons are newline-normalized.  Preserve a
    # semantically equal existing manifest byte-for-byte so a Windows CRLF
    # no-op apply cannot dirty a checkout solely by normalizing line endings.
    if not manifest.exists() or digest_bytes(manifest.read_bytes()) != digest_bytes(desired_manifest):
        manifest.write_bytes(desired_manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("dry-run", "apply", "check"))
    parser.add_argument("--target", type=Path, default=Path.cwd())
    parser.add_argument("--project-name")
    args = parser.parse_args()

    target = args.target.resolve()
    if not target.is_dir():
        parser.error(f"target is not a directory: {target}")
    project_name = (
        args.project_name
        or configured_project_name(target)
        or configured_legacy_project_name(target)
        or target.name.replace("-", " ").replace("_", " ").title()
    )
    actions, writes, delete_paths = plan(target, project_name)
    for action in actions:
        print(f"{action.classification}: {action.path.as_posix()} ({action.reason})")

    conflicts = [action for action in actions if action.classification == "conflict"]
    changes = [
        action
        for action in actions
        if action.classification in {"create", "update", "managed-block-update", "delete"}
    ]
    if conflicts:
        print("Refusing to apply because ownership conflicts require manual resolution.", file=sys.stderr)
        return 2
    if args.mode == "apply":
        apply(target, writes, delete_paths, project_name)
        print(f"Applied Pleiad {plugin_version()} to {target}")
        return 0
    if args.mode == "check" and changes:
        print(f"Drift detected: {len(changes)} managed change(s) required.", file=sys.stderr)
        return 1
    if args.mode == "check":
        print(f"Pleiad {plugin_version()} state is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
