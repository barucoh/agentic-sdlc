#!/usr/bin/env python3
"""Deterministically bootstrap, upgrade, or check repository-native Agentic SDLC state."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "skills" / "bootstrap-agentic-sdlc" / "assets" / "repository"
MANIFEST_PATH = Path(".agentic-sdlc/managed.json")
AGENTS_PATH = Path("AGENTS.md")
BLOCK_START = "<!-- agentic-sdlc:start -->"
BLOCK_END = "<!-- agentic-sdlc:end -->"

MANAGED_PATHS = tuple(
    Path(path)
    for path in (
        ".agentic-sdlc/config.yaml",
        ".agentic-sdlc/handoff.schema.json",
        ".agentic-sdlc/handoff-template.json",
        ".codex/agents/architecture.toml",
        ".codex/agents/coordinator.toml",
        ".codex/agents/implementation.toml",
        ".codex/agents/knowledge_steward.toml",
        ".codex/agents/product.toml",
        ".codex/agents/qa.toml",
        ".codex/agents/reviewer.toml",
        "docs/agentic-sdlc/coordination.md",
        "docs/agentic-sdlc/role-contracts.md",
    )
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
    if relative == Path(".agentic-sdlc/config.yaml"):
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
    path = target / MANIFEST_PATH
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"invalid": True}
    return value if isinstance(value, dict) else {"invalid": True}


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
    path = target / ".agentic-sdlc/config.yaml"
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
    return errors


def is_safe_managed_update(target: Path, relative: Path, installed: dict) -> bool:
    path = target / relative
    recorded = installed.get("files", {}).get(relative.as_posix())
    if recorded:
        return recorded == digest_path(path)
    if LEGACY_HASHES.get(relative) == digest_path(path):
        return True
    if relative == Path(".agentic-sdlc/config.yaml"):
        return recognized_legacy_config(path.read_text(encoding="utf-8"))
    first_line = path.read_text(encoding="utf-8").splitlines()[0] if path.stat().st_size else ""
    return first_line.startswith("# agentic-sdlc:managed") or first_line.startswith("<!-- agentic-sdlc:managed")


def desired_agents_text() -> str:
    source = (TEMPLATES / AGENTS_PATH).read_text(encoding="utf-8")
    start = source.index(BLOCK_START)
    end = source.index(BLOCK_END, start) + len(BLOCK_END)
    return source[start:end]


def merge_agents(existing: str, block: str) -> tuple[str | None, str]:
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


def plan(target: Path, project_name: str) -> tuple[list[Action], dict[Path, str], str | None]:
    installed = load_installed_manifest(target)
    actions: list[Action] = []
    writes: dict[Path, str] = {}
    for error in template_validation_errors(project_name):
        actions.append(Action("conflict", Path(".agentic-sdlc"), f"invalid release template: {error}"))
    if installed.get("invalid"):
        actions.append(Action("conflict", MANIFEST_PATH, "invalid managed-state manifest"))

    for relative in MANAGED_PATHS:
        desired = template_text(relative, project_name)
        destination = target / relative
        if not destination.exists():
            actions.append(Action("create", relative, "missing managed file"))
            writes[relative] = desired
        elif destination.read_text(encoding="utf-8") == desired:
            actions.append(Action("unchanged", relative, "matches release candidate"))
        elif is_safe_managed_update(target, relative, installed):
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
        recorded = installed.get("files", {}).get(OBSOLETE_REGISTRY.as_posix())
        text = obsolete.read_text(encoding="utf-8")
        if obsolete_hash == LEGACY_HASHES[OBSOLETE_REGISTRY] or recorded == obsolete_hash:
            actions.append(Action("delete", OBSOLETE_REGISTRY, "obsolete v0.2.0 role registry"))
            delete_obsolete = OBSOLETE_REGISTRY.as_posix()
        elif "config_file = \"agents/" in text:
            actions.append(Action("conflict", OBSOLETE_REGISTRY, "customized legacy role registry requires manual migration"))
        else:
            actions.append(Action("preserve", OBSOLETE_REGISTRY, "project-owned Codex configuration"))

    actions.sort(key=lambda action: (action.path.as_posix(), action.classification))
    return actions, writes, delete_obsolete


def managed_manifest(target: Path, project_name: str) -> str:
    files = {
        relative.as_posix(): digest_path(target / relative)
        for relative in MANAGED_PATHS
    }
    block_hash = digest_bytes(desired_agents_text().encode("utf-8"))
    value = {
        "schema_version": 1,
        "plugin_version": plugin_version(),
        "project_name": project_name,
        "files": files,
        "managed_blocks": {AGENTS_PATH.as_posix(): block_hash},
    }
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def apply(target: Path, writes: dict[Path, str], delete_obsolete: str | None, project_name: str) -> None:
    config_path = Path(".agentic-sdlc/config.yaml")
    config_content = writes.get(config_path)
    staged_writes = {path: content for path, content in writes.items() if path != config_path}
    for relative, content in sorted(staged_writes.items(), key=lambda item: item[0].as_posix()):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")
    if delete_obsolete:
        (target / delete_obsolete).unlink()
    for relative, content in staged_writes.items():
        if (target / relative).read_text(encoding="utf-8") != content:
            raise RuntimeError(f"post-write validation failed: {relative.as_posix()}")
    if config_content is not None:
        destination = target / config_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(config_content, encoding="utf-8", newline="\n")
        if destination.read_text(encoding="utf-8") != config_content:
            raise RuntimeError("post-write validation failed: .agentic-sdlc/config.yaml")
    manifest = target / MANIFEST_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(managed_manifest(target, project_name), encoding="utf-8", newline="\n")


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
        or target.name.replace("-", " ").replace("_", " ").title()
    )
    actions, writes, delete_obsolete = plan(target, project_name)
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
        apply(target, writes, delete_obsolete, project_name)
        print(f"Applied Agentic SDLC {plugin_version()} to {target}")
        return 0
    if args.mode == "check" and changes:
        print(f"Drift detected: {len(changes)} managed change(s) required.", file=sys.stderr)
        return 1
    if args.mode == "check":
        print(f"Agentic SDLC {plugin_version()} state is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
