"""The evidence ledger — facts the model did not author.

Every command the agent runs, every file it edits, and every VAL run is appended
here by hooks. `coder-ai prove` reads it. Nothing in this file comes from a sentence a
model wrote, which is the entire point: a claim can be checked against it.

Privacy follows the Project Tasks boundary: commands and paths, never output,
prompts, or secrets.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DIRECTORY = ".coder-ai/evidence"
CURRENT = "current"
MAX_RECORDS = 5000
MAX_FIELD = 600

COMMAND = "command"
EDIT = "edit"
VAL_RUN = "val_run"
NOTE = "note"

# Secret shapes must never enter a file we later render into a report.
_SECRET = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\b[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|APIKEY|API_KEY)[A-Z0-9_]*"
               r"\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
)
_TASK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class LedgerError(RuntimeError):
    """The ledger cannot be read or written safely."""


def redact(text: str) -> str:
    for pattern in _SECRET:
        text = pattern.sub("[redacted]", text)
    return text


@dataclass
class Record:
    kind: str
    at: float
    ok: bool = True
    command: str = ""
    exit: int | None = None
    paths: list[str] = field(default_factory=list)
    run: str = ""
    detail: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Record":
        known = {name: value[name] for name in cls.__dataclass_fields__ if name in value}
        known.setdefault("kind", NOTE)
        known.setdefault("at", 0.0)
        return cls(**known)

    def covers(self, path: str) -> bool:
        return any(item == path for item in self.paths)


def directory(project: Path) -> Path:
    return Path(project) / DIRECTORY


def _task_path(project: Path, task: str) -> Path:
    if not _TASK.match(task):
        raise LedgerError(f"unsafe task id: {task!r}")
    return directory(project) / f"{task}.jsonl"


def current_task(project: Path) -> str:
    marker = directory(project) / CURRENT
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return CURRENT
    return value if value and _TASK.match(value) else CURRENT


def start(project: Path, task: str) -> Path:
    """Begin a new task's ledger. Previous evidence is never counted for it."""
    if not _TASK.match(task):
        raise LedgerError(f"unsafe task id: {task!r}")
    target = directory(project)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    (target / CURRENT).write_text(task + "\n", encoding="utf-8")
    os.chmod(target / CURRENT, 0o600)
    path = _task_path(project, task)
    path.touch(exist_ok=True)
    os.chmod(path, 0o600)
    return path


def append(project: Path, kind: str, *, task: str | None = None, **fields: Any) -> Record:
    """Append one observed fact. Never raises into the caller's control flow."""
    task = task or current_task(project)
    path = _task_path(project, task)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    record = {"kind": str(kind)[:40], "at": round(time.time(), 3)}
    for key, value in fields.items():
        if isinstance(value, str):
            record[key] = redact(value)[:MAX_FIELD]
        elif isinstance(value, (list, tuple)):
            record[key] = [redact(str(item))[:MAX_FIELD] for item in list(value)[:100]]
        else:
            record[key] = value

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    return Record.from_dict(record)


def read(project: Path, task: str | None = None) -> list[Record]:
    task = task or current_task(project)
    try:
        path = _task_path(project, task)
    except LedgerError:
        return []
    if not path.is_file():
        return []
    records: list[Record] = []
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
                records.append(Record.from_dict(value))
    return records[-MAX_RECORDS:]


def exists(project: Path) -> bool:
    """Is anything recorded at all? An empty ledger means nothing is verified."""
    return bool(read(project))


def last_edit(records: Iterable[Record], paths: Iterable[str]) -> float:
    """When were these files last changed? Evidence older than this proves nothing."""
    wanted = set(paths)
    latest = 0.0
    for record in records:
        if record.kind != EDIT:
            continue
        if wanted & set(record.paths):
            latest = max(latest, record.at)
    return latest


def commands_matching(records: Iterable[Record], needles: Iterable[str]) -> list[Record]:
    """Observed commands whose text contains any of these fragments."""
    fragments = [item for item in needles if item]
    found = []
    for record in records:
        if record.kind != COMMAND or not record.command:
            continue
        if any(fragment in record.command for fragment in fragments):
            found.append(record)
    return found


def digest(records: Iterable[Record]) -> str:
    blob = "\n".join(f"{item.kind}:{item.at}:{item.command}" for item in records)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]
