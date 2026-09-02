#!/usr/bin/env python3
# coder-ai-os:generated
"""Mark UI validation pending after a matching edit without invoking a model."""

from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path


def _patterns(pattern: str) -> list[str]:
    start, end = pattern.find("{"), pattern.find("}")
    expanded = [pattern] if start < 0 or end < start else [
        pattern[:start] + item + pattern[end + 1:] for item in pattern[start + 1:end].split(",")
    ]
    return expanded + [item.replace("/**/", "/") for item in expanded if "/**/" in item]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        raw_path = str((payload.get("tool_input") or {}).get("file_path") or "")
    except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not raw_path:
        return 0
    root = Path.cwd().resolve()
    path = Path(raw_path)
    try:
        relative = path.resolve().relative_to(root).as_posix() if path.is_absolute() else path.as_posix().lstrip("./")
    except ValueError:
        return 0
    configs = [root / ".coder-ai/val/config.json", *sorted((root / ".coder-ai/val/apps").glob("*/config.json"))]
    for config in configs:
        if not config.is_file() or config.is_symlink():
            continue
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        globs = data.get("watchGlobs") or []
        if not any(fnmatch.fnmatchcase(relative, expanded) for pattern in globs for expanded in _patterns(str(pattern))):
            continue
        marker = config.parent / "ui-validation.pending"
        if marker.exists() and marker.is_symlink():
            continue
        marker.touch(exist_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
