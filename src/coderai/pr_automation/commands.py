"""`coder-ai pr status | attach | stop | log` (§68).

Operates on running and finished sessions alike: everything they need is on
disk, so a supervisor does not have to be alive to answer questions about it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from coderai.pr_automation import audit, daemon, ui
from coderai.pr_automation.findings import load as load_ledger
from coderai.pr_automation.state import (
    Session, list_sessions, read_json, sessions_root, session_dir,
)
from coderai.pr_automation.snapshot import Snapshot

ATTACH_INTERVAL = 2.0


def _match(target: str | None, root: Path | None = None) -> list[Session]:
    rows = list_sessions(root or sessions_root())
    if not target:
        return rows
    wanted = target.lstrip("#")
    matched = [item for item in rows if str(item.number) == wanted]
    if matched:
        return matched
    return [item for item in rows
            if f"{item.owner}/{item.repo}#{item.number}" == target
            or item.head_branch == target]


def _directory(session: Session, root: Path | None = None) -> Path:
    return session_dir(session.owner, session.repo, session.number, root=root)


def _snapshot(directory: Path) -> Snapshot | None:
    value = read_json(directory / "snapshot.json")
    if not isinstance(value, dict):
        return None
    try:
        return Snapshot.from_dict(value)
    except Exception:
        return None


def _last_action(directory: Path) -> str:
    for record in reversed(audit.read(directory, limit=200)):
        if record.get("event") == "ai_finished":
            provider = str(record.get("provider", "AI")).capitalize()
            return f"{provider}: {record.get('summary', '')}".strip()
        if record.get("event") == "push_complete":
            return f"pushed {str(record.get('commit', ''))[:7]}"
    return ""


def status(target: str | None = None, *, root: Path | None = None,
           out: Callable[[str], None] = print, as_json: bool = False) -> int:
    rows = _match(target, root)
    if as_json:
        import json as _json

        out(_json.dumps([item.to_dict() for item in rows], indent=2, sort_keys=True))
        return 0 if rows or not target else 1
    if target and not rows:
        out(f"coder-ai pr: no session for {target}")
        return 1
    if not target:
        out(ui.status_table(rows))
        return 0
    session = rows[0]
    directory = _directory(session, root)
    out(ui.waiting_pane(session, ledger=load_ledger(directory),
                        snapshot=_snapshot(directory),
                        last_action=_last_action(directory)))
    supervisor = daemon.read_pid(directory)
    if supervisor and supervisor.alive():
        out(f"supervisor: running (pid {supervisor.pid})")
    elif not session.is_terminal:
        out("supervisor: not running — resume with `coder-ai pr "
            f"{session.number} --watch 2h -- {session.provider}`")
    return 0


def log(target: str, *, root: Path | None = None, limit: int = 200,
        out: Callable[[str], None] = print) -> int:
    rows = _match(target, root)
    if not rows:
        out(f"coder-ai pr: no session for {target}")
        return 1
    directory = _directory(rows[0], root)
    out(ui.render_audit(audit.read(directory, limit=limit)))
    return 0


def stop(target: str, *, root: Path | None = None,
         out: Callable[[str], None] = print, sleep=time.sleep) -> int:
    rows = _match(target, root)
    if not rows:
        out(f"coder-ai pr: no session for {target}")
        return 1
    session = rows[0]
    directory = _directory(session, root)
    outcome = daemon.stop(directory, sleep=sleep)
    audit.append(directory, "user_stop", outcome=outcome)
    messages = {
        "STOPPED": f"coder-ai pr: stopped supervising #{session.number}",
        "TERMINATED": f"coder-ai pr: terminated the supervisor for #{session.number}",
        "NOT_RUNNING": (f"coder-ai pr: no supervisor was running for #{session.number}; "
                        "the stop request is recorded"),
        "UNRESPONSIVE": (f"coder-ai pr: the supervisor for #{session.number} is not responding; "
                         "it will exit at its next safe boundary"),
    }
    out(messages.get(outcome, outcome))
    return 0 if outcome != "UNRESPONSIVE" else 1


def attach(target: str, *, root: Path | None = None,
           out: Callable[[str], None] = print, sleep=time.sleep,
           iterations: int | None = None) -> int:
    """Stream a session's status view. Detaching never stops the session."""
    rows = _match(target, root)
    if not rows:
        out(f"coder-ai pr: no session for {target}")
        return 1
    session = rows[0]
    directory = _directory(session, root)
    seen = 0
    count = 0
    while iterations is None or count < iterations:
        count += 1
        from coderai.pr_automation.state import load_session

        current = load_session(session.owner, session.repo, session.number, root=root)
        if current is None:
            out("coder-ai pr: the session state disappeared")
            return 1
        for record in audit.tail(directory, after_seq=seen):
            seen = max(seen, int(record.get("seq", 0)))
            if record.get("event") in {"ai_wake", "ai_finished", "push_complete",
                                       "session_finished", "guard_deny"}:
                out(ui.render_audit([record]).rstrip())
        if current.is_terminal:
            out(ui.waiting_pane(current, ledger=load_ledger(directory),
                                snapshot=_snapshot(directory)))
            return 0
        if iterations is None or count < iterations:
            sleep(ATTACH_INTERVAL)
    out(ui.waiting_pane(session, ledger=load_ledger(directory),
                        snapshot=_snapshot(directory),
                        last_action=_last_action(directory)))
    return 0
