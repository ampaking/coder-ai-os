"""What the engineer sees (§69, §70).

Real engineering narration while the AI works; a quiet status pane while it
sleeps. Poll ticks, backoff and snapshot digests go to audit.jsonl — never here.
"""

from __future__ import annotations

import shutil
import time
from typing import Iterable

from coderai.pr_automation.findings import Ledger
from coderai.pr_automation.snapshot import Snapshot
from coderai.pr_automation.state import Session

MIN_WIDTH = 40
MAX_WIDTH = 100


def width(default: int = 80) -> int:
    try:
        value = shutil.get_terminal_size((default, 24)).columns
    except OSError:
        value = default
    return max(MIN_WIDTH, min(MAX_WIDTH, value))


def _field(label: str, value: str) -> str:
    return f"{label:<11}{value}"


def _age(moment: float | None, now: float | None = None) -> str:
    if not moment:
        return "—"
    seconds = int((time.time() if now is None else now) - moment)
    if seconds < 90:
        return f"{max(0, seconds)}s ago"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def working_pane(session: Session, *, task: str, changed: Iterable[str] = (),
                 validation: Iterable[tuple[str, str]] = ()) -> str:
    lines = [
        f"coder-ai · PR #{session.number}",
        "",
        _field("State", "WORKING"),
        _field("AI", session.provider.capitalize()),
        _field("Task", task),
        _field("HEAD", session.head_sha[:7] or "—"),
    ]
    changed = list(changed)
    if changed:
        lines += ["", "Changed", *[f"{name}" for name in changed[:20]]]
    checks = list(validation)
    if checks:
        lines += ["", "Validation",
                  *[f"{name:<16} {status}" for name, status in checks[:20]]]
    return "\n".join(lines) + "\n"


def waiting_pane(session: Session, *, ledger: Ledger | None = None,
                 snapshot: Snapshot | None = None, last_action: str = "",
                 now: float | None = None) -> str:
    open_findings = len(ledger.open_findings()) if ledger else 0
    ci = snapshot.ci_state if snapshot else (session.last_ci_state or "unknown")
    lines = [
        f"coder-ai · PR #{session.number}",
        "",
        _field("State", session.state),
        _field("HEAD", session.head_sha[:7] or "—"),
        _field("CI", ci),
        _field("Findings", f"{open_findings} open"),
        _field("AI", "sleeping"),
    ]
    if last_action:
        lines += ["", "Last action", last_action]
    if session.watch_deadline:
        remaining = int(session.watch_deadline - (time.time() if now is None else now))
        if remaining > 0:
            lines += ["", f"Watching for new PR activity… ({remaining // 60}m left)"]
        else:
            lines += ["", "Watch window ended."]
    else:
        lines += ["", "Watching for new PR activity…"]
    return "\n".join(lines) + "\n"


def status_table(sessions: Iterable[Session], now: float | None = None) -> str:
    """The multi-PR supervisor view (§74)."""
    rows = list(sessions)
    if not rows:
        return "coder-ai: no supervised pull requests.\n"
    lines = ["coder-ai Supervisor", ""]
    for item in rows:
        marker = "·" if item.is_terminal else "▸"
        lines.append(f"{marker} {item.owner}/{item.repo}#{item.number:<6} "
                     f"{item.state:<20} {item.provider:<7} "
                     f"{_age(item.updated_at, now)}")
    return "\n".join(lines) + "\n"


def render_audit(records: Iterable[dict], *, limit: int = 200) -> str:
    """The audit log, readable — the operator's view, not the engineer's pane."""
    rows = list(records)[-limit:]
    if not rows:
        return "(no recorded events)\n"
    lines = []
    for item in rows:
        stamp = time.strftime("%H:%M:%S", time.localtime(item.get("at", 0)))
        event = str(item.get("event", ""))
        detail = " ".join(
            f"{key}={item[key]}" for key in sorted(item)
            if key not in {"at", "event", "seq", "pid"} and item[key] not in (None, "", [])
        )
        lines.append(f"{stamp}  {event:<26} {detail}"[:400].rstrip())
    return "\n".join(lines) + "\n"
