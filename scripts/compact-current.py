#!/usr/bin/env python3
# coder-ai-os:generated
"""Bound accumulated CURRENT.md history while preserving its newest checkpoint."""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

LIMIT = 8_192


def compact(path: Path) -> bool:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= LIMIT:
        return False
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    sections = [index for index, line in enumerate(lines) if line.startswith("# ")]
    if len(sections) < 3:
        return False
    newest_start, newest_end = sections[1], sections[2]
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    history = path.parent / "history"
    history.mkdir(mode=0o700, exist_ok=True)
    archive = history / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{digest}.md"
    archive.write_text(text, encoding="utf-8")
    current = "".join(lines[:newest_start] + lines[newest_start:newest_end])
    current += f"\nPrevious checkpoints: [archived locally](history/{archive.name})\n"
    path.write_text(current, encoding="utf-8")
    return True


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".ai/memory/CURRENT.md")
    raise SystemExit(0 if compact(target) or target.exists() else 1)
