"""Verified project identity and confined local state paths."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


class ProjectError(RuntimeError):
    """Raised when project identity or local state is unsafe."""


def _git_output(project: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def resolve_project(start: str | Path = ".") -> Path:
    candidate = Path(start).resolve(strict=True)
    if not candidate.is_dir():
        raise ProjectError(f"not a directory: {candidate}")
    git_root = _git_output(candidate, "rev-parse", "--show-toplevel")
    return Path(git_root).resolve(strict=True) if git_root else candidate


def root_hash(project: Path) -> str:
    return f"sha256:{hashlib.sha256(str(project).encode()).hexdigest()}"


def _load_json(file_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"cannot read valid JSON: {file_path}") from exc
    if not isinstance(value, dict):
        raise ProjectError(f"expected JSON object: {file_path}")
    return value


def require_identity(project: Path) -> dict[str, Any]:
    identity_path = project / ".coder-ai" / "identity.json"
    reject_symlinks(project, identity_path)
    if not identity_path.is_file():
        raise ProjectError("project is not set up; run: coder-ai-os setup")
    identity = _load_json(identity_path)
    expected = root_hash(project)
    if identity.get("git_root_hash") != expected:
        raise ProjectError("project identity mismatch; run coder-ai-os sync before using tasks")
    return identity


def reject_symlinks(project: Path, target: Path) -> None:
    project = project.resolve(strict=True)
    try:
        relative = target.relative_to(project)
    except ValueError as exc:
        raise ProjectError(f"path escapes project: {target}") from exc
    current = project
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ProjectError(f"refusing symlink path: {current}")


def state_dir(project: Path) -> Path:
    target = project / ".coder-ai" / "tasks"
    reject_symlinks(project, target)
    return target


def settings_path(project: Path) -> Path:
    return state_dir(project) / "settings.json"


def load_settings(project: Path) -> dict[str, Any] | None:
    file_path = settings_path(project)
    if not file_path.exists():
        return None
    if not file_path.is_file():
        raise ProjectError(f"settings target is not a file: {file_path}")
    settings = _load_json(file_path)
    if settings.get("projectHash") != root_hash(project):
        raise ProjectError("task settings belong to another project")
    return settings


def write_settings(project: Path, settings: dict[str, Any]) -> None:
    directory = state_dir(project)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    target = directory / "settings.json"
    reject_symlinks(project, target)
    temporary = directory / ".settings.tmp"
    reject_symlinks(project, temporary)
    payload = json.dumps(settings, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, target)
    os.chmod(target, 0o600)
