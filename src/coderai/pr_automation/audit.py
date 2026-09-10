"""Append-only operational provenance for a PR session.

Detailed machine information (provider, model, role, decisions, denials, pushes)
belongs here — never in Git history (§31, §71). One JSON object per line, with a
monotonic sequence, written under an advisory lock so concurrent appends from the
supervisor, the git guard, and a worker cannot interleave.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Iterator

AUDIT_FILE = "audit.jsonl"
MAX_FIELD = 4000
MAX_LINE = 32_000


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_FIELD]
    if isinstance(value, dict):
        return {str(key)[:200]: _clip(item) for key, item in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        return [_clip(item) for item in list(value)[:100]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_FIELD]


def append(directory: Path, event: str, **fields: Any) -> dict[str, Any]:
    """Append one audit event. Returns the written record."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / AUDIT_FILE
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        sequence = _count_lines(path) + 1
        record = {"seq": sequence, "at": round(time.time(), 3), "event": str(event)[:120],
                  "pid": os.getpid(), **{key: _clip(value) for key, value in fields.items()}}
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))[:MAX_LINE] + "\n"
        os.write(descriptor, line.encode("utf-8"))
        os.fsync(descriptor)
        return record
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def read(directory: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = directory / AUDIT_FILE
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records[-limit:] if limit else records


def tail(directory: Path, after_seq: int = 0) -> Iterator[dict[str, Any]]:
    for record in read(directory):
        if int(record.get("seq", 0)) > after_seq:
            yield record
