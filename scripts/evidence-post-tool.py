#!/usr/bin/env python3
# coder-ai-os:generated
"""Record what a tool actually did, so a claim can be checked against it.

Runs as a PostToolUse hook. Deterministic, command-only, no model, no network.
It must never fail the tool call it observes: every error path exits 0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MAX_COMMAND = 600


def _project(payload: dict) -> Path:
    for key in ("cwd", "project_dir", "projectDir"):
        value = payload.get(key)
        if value:
            return Path(str(value))
    return Path.cwd()


def _outcome(payload: dict) -> tuple[int | None, bool]:
    """(exit code, ok). Claude does not always report a code; an error field means failure."""
    response = payload.get("tool_response") or payload.get("toolResponse") or {}
    if not isinstance(response, dict):
        return None, "error" not in str(response).lower()[:200]
    for key in ("exit_code", "exitCode", "returncode", "status"):
        if isinstance(response.get(key), int):
            code = int(response[key])
            return code, code == 0
    if response.get("is_error") or response.get("isError") or response.get("error"):
        return None, False
    if response.get("interrupted"):
        return None, False
    return None, True


def _paths(payload: dict) -> list[str]:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return []
    found = []
    for key in ("file_path", "filePath", "path", "notebook_path"):
        value = tool_input.get(key)
        if value:
            found.append(str(value))
    for item in tool_input.get("edits") or []:
        if isinstance(item, dict) and item.get("file_path"):
            found.append(str(item["file_path"]))
    return found[:20]


def _relative(project: Path, paths: list[str]) -> list[str]:
    out = []
    for raw in paths:
        path = Path(raw)
        try:
            out.append(path.resolve().relative_to(project.resolve()).as_posix()
                       if path.is_absolute() else path.as_posix().lstrip("./"))
        except (ValueError, OSError):
            continue
    return out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    try:
        project = _project(payload)
        root = os.environ.get("CODER_AI_SRC") or str(
            Path(__file__).resolve().parents[1] / "src")
        if root not in sys.path:
            sys.path.insert(0, root)
        from coderai.evidence import ledger

        if not (project / ".coder-ai").is_dir():
            return 0

        tool = str(payload.get("tool_name") or payload.get("toolName") or "")
        if tool == "Bash":
            command = str((payload.get("tool_input") or {}).get("command") or "")[:MAX_COMMAND]
            if not command:
                return 0
            code, ok = _outcome(payload)
            ledger.append(project, ledger.COMMAND, command=command, exit=code, ok=ok)
        elif tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
            paths = _relative(project, _paths(payload))
            if paths:
                ledger.append(project, ledger.EDIT, paths=paths, ok=True)
    except Exception:
        return 0  # observing must never break the work being observed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
