"""Background supervision and the stop channel (§68, §76).

`--bg` detaches a supervisor; `coder-ai pr stop` asks it to finish at a safe
boundary — never mid-push. A stop request is a file, so it works across
processes without a socket, and a recycled pid cannot impersonate the owner.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from coderai.pr_automation.state import (
    Session, StateError, _process_start, process_alive, read_json, write_json,
)

STOP_FILE = "stop.request"
PID_FILE = "supervisor.pid"
LOG_FILE = "supervisor.log"

STOP_GRACE = 30.0


@dataclass
class Supervisor:
    pid: int
    started: str
    at: float

    def alive(self) -> bool:
        return process_alive(self.pid, self.started)


def write_pid(directory: Path) -> Supervisor:
    record = Supervisor(pid=os.getpid(), started=_process_start(os.getpid()),
                        at=time.time())
    write_json(directory / PID_FILE, {"pid": record.pid, "started": record.started,
                                      "at": record.at})
    os.chmod(directory / PID_FILE, 0o600)
    return record


def read_pid(directory: Path) -> Supervisor | None:
    value = read_json(directory / PID_FILE)
    if not isinstance(value, dict):
        return None
    return Supervisor(pid=int(value.get("pid") or 0), started=str(value.get("started") or ""),
                      at=float(value.get("at") or 0.0))


def clear_pid(directory: Path) -> None:
    (directory / PID_FILE).unlink(missing_ok=True)


def request_stop(directory: Path, *, by: str = "user") -> None:
    write_json(directory / STOP_FILE, {"by": by, "at": time.time()})
    os.chmod(directory / STOP_FILE, 0o600)


def stop_requested(directory: Path) -> bool:
    return (directory / STOP_FILE).is_file()


def clear_stop(directory: Path) -> None:
    (directory / STOP_FILE).unlink(missing_ok=True)


def stop(directory: Path, *, grace: float = STOP_GRACE,
         sleep=time.sleep) -> str:
    """Ask a running supervisor to finish; escalate only if it ignores us."""
    request_stop(directory)
    supervisor = read_pid(directory)
    if supervisor is None or not supervisor.alive():
        clear_pid(directory)
        return "NOT_RUNNING"

    deadline = time.time() + grace
    while time.time() < deadline:
        if not supervisor.alive():
            clear_pid(directory)
            return "STOPPED"
        sleep(0.2)

    # It is still working (a push transaction, say). SIGTERM lets it unwind.
    try:
        os.kill(supervisor.pid, signal.SIGTERM)
    except OSError:
        clear_pid(directory)
        return "STOPPED"
    deadline = time.time() + grace
    while time.time() < deadline:
        if not supervisor.alive():
            clear_pid(directory)
            return "TERMINATED"
        sleep(0.2)
    return "UNRESPONSIVE"


def spawn(argv: list[str], directory: Path, *, cwd: Path) -> int:
    """Detach a supervisor: it outlives this shell, but never its own deadline."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = directory / LOG_FILE
    handle = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        child = subprocess.Popen(
            argv, cwd=str(cwd), stdout=handle, stderr=handle,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    finally:
        os.close(handle)
    return child.pid
