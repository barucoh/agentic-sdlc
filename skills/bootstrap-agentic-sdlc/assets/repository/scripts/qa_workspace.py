# agentic-sdlc:managed runtime/v1
"""Repository-native checks for an isolated QA verification worktree."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


class QAWorkspaceError(ValueError):
    """Raised when a QA workspace violates the repository verification boundary."""


@dataclass(frozen=True)
class WorkspaceSnapshot:
    head: str
    branch: str
    status: tuple[str, ...]
    source_diff: str


@dataclass(frozen=True)
class WorkspaceVerification:
    passed: bool
    errors: tuple[str, ...]
    cleanup_responsibility: str
    before: WorkspaceSnapshot
    after: WorkspaceSnapshot
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=workspace, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise QAWorkspaceError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _branch(workspace: Path) -> str:
    result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    # A detached exact-head QA worktree is the preferred identity; symbolic-ref
    # returns status 1 for that expected condition.
    if result.returncode == 1:
        return ""
    if result.returncode:
        raise QAWorkspaceError(result.stderr.strip() or "unable to inspect QA branch identity")
    return result.stdout.strip()


def _under_declared_output(path: str, declared_outputs: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return any(normalized == output or normalized.startswith(output + "/") for output in declared_outputs)


def _status_paths(status: Iterable[str], declared_outputs: tuple[str, ...]) -> tuple[str, ...]:
    kept: list[str] = []
    for line in status:
        path = line[3:].split(" -> ", 1)[-1] if len(line) >= 3 else line
        if not _under_declared_output(path, declared_outputs):
            kept.append(line)
    return tuple(kept)


def capture_snapshot(workspace: str | Path, declared_outputs: Iterable[str] = ()) -> WorkspaceSnapshot:
    root = Path(workspace)
    outputs = tuple(sorted(output.replace("\\", "/").strip("/.") for output in declared_outputs if output.strip("/.")))
    status = tuple(line for line in _git(root, "status", "--porcelain=v1").splitlines() if line)
    diff_args = ["diff", "--binary", "--"]
    diff_args.extend([".", *[f":(exclude){output}" for output in outputs]])
    return WorkspaceSnapshot(
        head=_git(root, "rev-parse", "HEAD").strip(),
        branch=_branch(root),
        status=_status_paths(status, outputs),
        source_diff=_git(root, *diff_args),
    )


def verify_qa_workspace(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
    *,
    expected_sha: str,
    implementation_branch: str,
    declared_outputs: Iterable[str] = (),
    intents: Iterable[str] = (),
    command: Iterable[str] = (),
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
) -> WorkspaceVerification:
    outputs = tuple(sorted(output.replace("\\", "/").strip("/.") for output in declared_outputs if output.strip("/.")))
    errors: list[str] = []
    if before.head != expected_sha:
        errors.append("QA workspace pre-snapshot is not the exact expected implementation commit")
    if after.head != expected_sha:
        errors.append("QA workspace HEAD is not the exact expected implementation commit")
    if before.head != after.head:
        errors.append("QA workspace HEAD changed during behavioral verification")
    if before.branch and before.branch == implementation_branch:
        errors.append("QA workspace pre-snapshot must be detached or use a non-Implementation branch")
    if after.branch and after.branch == implementation_branch:
        errors.append("QA workspace must be detached or use a non-Implementation branch")
    forbidden = {"source-edit", "source_mutation", "commit", "push"}
    bad_intents = sorted(set(intents) & forbidden)
    if bad_intents:
        errors.append("QA intent contains forbidden actions: " + ", ".join(bad_intents))
    if before.status or before.source_diff:
        errors.append("QA workspace has pre-existing tracked source changes outside declared outputs")
    if before.status != after.status or before.source_diff != after.source_diff:
        errors.append("QA changed source or committed content outside declared cache/build/test outputs")
    if exit_code is not None and exit_code != 0:
        errors.append(f"behavioral command failed with exit code {exit_code}")
    return WorkspaceVerification(
        passed=not errors,
        errors=tuple(errors),
        cleanup_responsibility="Codex host/task-control provisions and removes the disposable worktree; this repository check verifies identity and drift but does not provision or delete it.",
        before=before,
        after=after,
        command=tuple(command),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an isolated QA worktree")
    parser.add_argument("workspace")
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--implementation-branch", required=True)
    parser.add_argument("--declared-output", action="append", default=[])
    parser.add_argument("--intent", action="append", default=[])
    raw = list(argv if argv is not None else sys.argv[1:])
    if "--" not in raw:
        parser.error("a behavioral command is required after --")
    delimiter = raw.index("--")
    args = parser.parse_args(raw[:delimiter])
    command = raw[delimiter + 1 :]
    if not command:
        parser.error("a behavioral command is required after --")
    before = capture_snapshot(args.workspace, args.declared_output)
    preflight = verify_qa_workspace(
        before,
        before,
        expected_sha=args.expected_sha,
        implementation_branch=args.implementation_branch,
        declared_outputs=args.declared_output,
        intents=args.intent,
        command=command,
        exit_code=0,
    )
    if not preflight.passed:
        print(json.dumps(asdict(preflight), indent=2))
        return 2
    completed = subprocess.run(command, cwd=args.workspace, text=True, capture_output=True, check=False, shell=False)
    after = capture_snapshot(args.workspace, args.declared_output)
    result = verify_qa_workspace(
        before,
        after,
        expected_sha=args.expected_sha,
        implementation_branch=args.implementation_branch,
        declared_outputs=args.declared_output,
        intents=args.intent,
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
