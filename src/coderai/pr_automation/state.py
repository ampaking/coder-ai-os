"""Confined, versioned session state for one supervised Pull Request.

    ~/.coder-ai/pr-sessions/<owner>/<repo>/<pr>/
        session.json   snapshot.json   findings.json
        provider-health.json   audit.jsonl   runs/

Writes are atomic (temp + fsync + rename), directories are 0700, files 0600, and
every path component is symlink-checked before use. Stopping coder-ai-os must never
destroy context, so everything needed to resume lives here.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STATE_ROOT_ENV = "CODER_AI_HOME"
DEFAULT_HOME = "~/.coder-ai"
LEGACY_HOME = "~/.coder-ai-os"

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class StateError(RuntimeError):
    """Session state cannot be read or written safely."""


# ---------------------------------------------------------------- paths


def coder_ai_home() -> Path:
    """Machine state. An existing install keeps working under its old name."""
    override = os.environ.get(STATE_ROOT_ENV)
    if override:
        return Path(override)
    current = Path(os.path.expanduser(DEFAULT_HOME))
    if not current.exists():
        legacy = Path(os.path.expanduser(LEGACY_HOME))
        if legacy.is_dir():
            return legacy
    return current


def sessions_root() -> Path:
    return coder_ai_home() / "pr-sessions"


def worktrees_root() -> Path:
    return coder_ai_home() / "pr-worktrees"


def _safe(component: str, label: str) -> str:
    if not _SAFE_COMPONENT.match(component) or component in {".", ".."}:
        raise StateError(f"unsafe {label} in state path: {component!r}")
    return component


def _resolve_lexically(path: Path) -> Path:
    """Resolve the deepest existing ancestor, then re-append the missing parts.

    Path.resolve() on a not-yet-created path is fine, but on macOS the state root
    itself is often a symlink (/var -> /private/var), so comparing a resolved
    target against an unresolved root would falsely read as an escape.
    """
    missing: list[str] = []
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            break
        missing.append(current.name)
        current = parent
    resolved = current.resolve()
    for name in reversed(missing):
        resolved = resolved / name
    return resolved


def reject_symlinks(root: Path, target: Path) -> None:
    """Refuse a path whose components include a symlink, or that escapes root.

    Two independent checks: component-wise (catches a symlink that still points
    inside the root) and containment after resolution (catches one that escapes).
    """
    try:
        relative = target.relative_to(root)
    except ValueError:
        try:
            relative = _resolve_lexically(target).relative_to(_resolve_lexically(root))
        except ValueError as exc:
            raise StateError(f"path escapes the coder-ai-os state root: {target}") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise StateError(f"refusing symlink path component: {current}")
    resolved_root = _resolve_lexically(root)
    resolved_target = _resolve_lexically(target)
    if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
        raise StateError(f"path escapes the coder-ai-os state root: {target}")


def session_dir(owner: str, repo: str, number: int, *, root: Path | None = None) -> Path:
    base = (root or sessions_root()).expanduser()
    target = (base / _safe(owner, "owner") / _safe(repo, "repository")
              / _safe(str(int(number)), "pull request"))
    reject_symlinks(base, target)
    return target


def ensure_dir(path: Path, root: Path | None = None) -> Path:
    base = (root or sessions_root()).expanduser()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    reject_symlinks(base, path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


# ---------------------------------------------------------------- atomic io


TEMP_SWEEP_AGE = 3600


def _sweep_temporaries(directory: Path, name: str, now: float | None = None) -> None:
    """Remove temp files a SIGKILLed writer could not clean up (its `finally` never ran)."""
    moment = time.time() if now is None else now
    for stale in directory.glob(f".{name}.*.tmp"):
        try:
            if moment - stale.stat().st_mtime > TEMP_SWEEP_AGE:
                stale.unlink(missing_ok=True)
        except OSError:
            continue


def write_json(target: Path, value: Any) -> None:
    """Write JSON atomically: a crash mid-write can never truncate the file."""
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _sweep_temporaries(target.parent, target.name)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(target: Path) -> Any:
    if not target.exists():
        return None
    if target.is_symlink() or not target.is_file():
        raise StateError(f"refusing to read a non-regular state file: {target}")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"corrupt state file: {target} ({exc})") from exc


# ---------------------------------------------------------------- session


@dataclass
class Session:
    """Everything needed to resume supervision of one Pull Request."""

    host: str
    owner: str
    repo: str
    number: int
    head_branch: str
    base_branch: str
    remote: str
    provider: str
    provider_argv: list[str] = field(default_factory=list)

    url: str = ""
    title: str = ""
    author: str = ""
    head_sha: str = ""

    schema_version: int = SCHEMA_VERSION
    state: str = "OPEN"
    terminal_reason: str = ""

    watch_mode: str = "bounded"
    watch_seconds: int | None = 3600
    watch_deadline: float | None = None

    background: bool = False
    pid: int | None = None

    worktree: str = ""
    # The local checkout this session is driving. A PR is one lifecycle object, so
    # its state is shared across checkouts, but the worktree belongs to exactly one.
    source_repo: str = ""
    last_snapshot_digest: str = ""
    last_push_sha: str = ""
    last_push_at: float | None = None
    last_ci_state: str = ""
    wake_count: int = 0
    # Repeated identical CI failures must not loop forever: {head:checks -> attempts}
    ci_attempts: dict[str, int] = field(default_factory=dict)

    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def is_terminal(self) -> bool:
        return self.state in {"MERGED", "CLOSED", "WATCH_TIMEOUT", "USER_STOP"}

    def directory(self, root: Path | None = None) -> Path:
        return session_dir(self.owner, self.repo, self.number, root=root)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Session":
        version = value.get("schema_version")
        if not isinstance(version, int):
            raise StateError("session state has no schema_version")
        if version > SCHEMA_VERSION:
            raise StateError(
                f"session was written by a newer coder-ai-os (schema {version} > {SCHEMA_VERSION}); "
                "upgrade coder-ai-os or delete the session directory"
            )
        known = {item.name for item in cls.__dataclass_fields__.values()}
        unknown = set(value) - known
        payload = {key: item for key, item in value.items() if key in known}
        session = cls(**payload)
        if unknown:
            # Forward-compatible within a version: ignore extra keys, never guess.
            pass
        return session


def new_session(pull_request: Any, *, provider: str, provider_argv: list[str],
                watch_mode: str, watch_seconds: int | None, background: bool = False,
                now: float | None = None) -> Session:
    moment = time.time() if now is None else now
    deadline = None
    if watch_mode == "bounded" and watch_seconds:
        deadline = moment + float(watch_seconds)
    return Session(
        host=pull_request.host, owner=pull_request.owner, repo=pull_request.repo,
        number=pull_request.number, head_branch=pull_request.head_branch,
        base_branch=pull_request.base_branch, remote=pull_request.remote,
        provider=provider, provider_argv=list(provider_argv),
        url=pull_request.url, title=pull_request.title, author=pull_request.author,
        head_sha=pull_request.head_sha, state="OPEN",
        watch_mode=watch_mode, watch_seconds=watch_seconds, watch_deadline=deadline,
        background=background, pid=os.getpid(),
        created_at=moment, updated_at=moment,
    )


def save_session(session: Session, *, root: Path | None = None, now: float | None = None) -> Path:
    directory = ensure_dir(session.directory(root), root)
    session.updated_at = time.time() if now is None else now
    write_json(directory / "session.json", session.to_dict())
    return directory


def load_session(owner: str, repo: str, number: int, *, root: Path | None = None) -> Session | None:
    directory = session_dir(owner, repo, number, root=root)
    value = read_json(directory / "session.json")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise StateError("session.json is not a JSON object")
    session = Session.from_dict(value)
    if (session.owner.lower(), session.repo.lower(), session.number) != (
            owner.lower(), repo.lower(), int(number)):
        raise StateError(
            f"session state belongs to {session.slug}, not {owner}/{repo}#{number} "
            "(copied state directory?)"
        )
    return session


def list_sessions(root: Path | None = None) -> list[Session]:
    base = (root or sessions_root()).expanduser()
    if not base.is_dir():
        return []
    found: list[Session] = []
    for path in sorted(base.glob("*/*/*/session.json")):
        try:
            value = read_json(path)
            if isinstance(value, dict):
                found.append(Session.from_dict(value))
        except StateError:
            continue  # a corrupt or future-version session must not break `status`
    return found


def resume(owner: str, repo: str, number: int, *, root: Path | None = None) -> tuple[Session | None, str]:
    """Load a prior session. Returns (session, reason) — reason names why not."""
    try:
        session = load_session(owner, repo, number, root=root)
    except StateError as error:
        return None, str(error)
    if session is None:
        return None, "NO_PRIOR_SESSION"
    if session.is_terminal:
        return session, f"PRIOR_SESSION_TERMINAL:{session.state}"
    return session, "RESUMED"


# ---------------------------------------------------------------- lock


def process_info(pid: int) -> tuple[str, str]:
    """(state, start time) for a pid — "" when it cannot be read."""
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "state=", "-o", "lstart="],
                                capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    line = result.stdout.strip()
    if not line:
        return "", ""
    state, _, started = line.partition(" ")
    return state.strip(), started.strip()


def _process_start(pid: int) -> str:
    """Process start time, so a recycled pid cannot impersonate a lock owner."""
    return process_info(pid)[1]


def process_alive(pid: int, started: str = "") -> bool:
    """Is this exactly the process we recorded, and still running?

    `os.kill(pid, 0)` is not enough: an unreaped child is a zombie, which still
    accepts signal 0 while being quite dead. A supervisor that exited would look
    alive to its own test harness.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    state, current = process_info(pid)
    if state.upper().startswith("Z"):
        return False
    if started and current and current != started:
        return False  # pid was recycled
    return True


def _alive(pid: int, started: str) -> bool:
    return process_alive(pid, started)


class SessionLock:
    """One supervisor per PR. Stale locks (dead or recycled pid) are reclaimed."""

    def __init__(self, directory: Path, *, root: Path | None = None) -> None:
        self.directory = directory
        self.root = root
        self.path = directory / "session.lock"
        self.held = False

    def owner(self) -> dict[str, Any] | None:
        try:
            value = read_json(self.path)
        except StateError:
            return None
        return value if isinstance(value, dict) else None

    def acquire(self) -> "SessionLock":
        ensure_dir(self.directory, self.root)
        payload = {
            "pid": os.getpid(),
            "started": _process_start(os.getpid()),
            "host": os.uname().nodename,
            "at": time.time(),
        }
        for attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                current = self.owner()
                if current is None:
                    self.path.unlink(missing_ok=True)
                    continue
                pid = int(current.get("pid") or 0)
                same_host = current.get("host") == payload["host"]
                if same_host and not _alive(pid, str(current.get("started") or "")):
                    self.path.unlink(missing_ok=True)
                    continue
                raise StateError(
                    f"this pull request is already supervised by pid {pid}"
                    f"{'' if same_host else ' on ' + str(current.get('host'))}; "
                    "use `coder-ai pr status` or `coder-ai pr stop`"
                )
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.held = True
                return self
        raise StateError("could not acquire the session lock")

    def release(self) -> None:
        if not self.held:
            return
        current = self.owner()
        if current and int(current.get("pid") or 0) == os.getpid():
            self.path.unlink(missing_ok=True)
        self.held = False

    def __enter__(self) -> "SessionLock":
        self.acquire()
        self._previous = {
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGINT: signal.getsignal(signal.SIGINT),
        }
        for number in list(self._previous):
            try:
                signal.signal(number, self._on_signal)
            except ValueError:
                self._previous.pop(number, None)  # not the main thread
        return self

    def _on_signal(self, signum: int, frame: Any) -> None:
        self.release()
        previous = getattr(self, "_previous", {}).get(signum)
        if callable(previous):
            previous(signum, frame)
            return
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def __exit__(self, *exc: Any) -> None:
        for number, handler in getattr(self, "_previous", {}).items():
            try:
                signal.signal(number, handler)
            except (ValueError, TypeError):
                pass
        self.release()
