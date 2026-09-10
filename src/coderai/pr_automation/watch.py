"""The supervisor loop: cheap while the AI sleeps (§10, §11, §59, §66, §75).

The product's resource claim lives here. An idle PR must cost zero model calls,
zero repo scans and zero test runs — only `gh` polls on an adaptive schedule.

Both the clock and the poller are injected, so the whole loop is testable in
milliseconds without a single real sleep.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from coderai.pr_automation import audit, findings as findings_module, states
from coderai.pr_automation.delta import Delta, compare
from coderai.pr_automation.findings import Ledger
from coderai.pr_automation.snapshot import Snapshot, SnapshotError
from coderai.pr_automation.state import Session, save_session, write_json

# §66 adaptive polling — seconds.
POLL_ACTIVE = 45          # just pushed, or CI running
POLL_REVIEW = 180         # waiting on an active reviewer
POLL_IDLE = 600           # long idle
POLL_READY = 900          # READY but still open
POLL_BACKOFF_MAX = 900    # gh errors back off; they must never spin

SINGLE_PASS = "SINGLE_PASS"
STOPPED = "USER_STOP"


class Clock(Protocol):
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


@dataclass
class WatchResult:
    reason: str
    state: str
    polls: int = 0
    wakes: int = 0
    errors: int = 0
    last_snapshot: Snapshot | None = None
    history: list[str] = field(default_factory=list)


def poll_interval(state: str, *, recently_active: bool = False) -> int:
    if recently_active or state == states.WAITING_FOR_CI:
        return POLL_ACTIVE
    if state in {states.OPEN, states.WORKING}:
        return POLL_REVIEW
    if state == states.WAITING_FOR_REVIEW:
        return POLL_REVIEW
    if state == states.READY:
        return POLL_READY
    return POLL_IDLE


def run_watch(session: Session, *, collect: Callable[[], Snapshot],
              wake: Callable[[Snapshot, Delta], str] | None = None,
              clock: Clock | None = None, directory: Path | None = None,
              ledger: Ledger | None = None, stop: Callable[[], bool] | None = None,
              state_root: Path | None = None,
              own_comment_ids: set[str] | None = None,
              own_head_shas: set[str] | None = None) -> WatchResult:
    """Watch one PR until a terminal state, the watch deadline, or a stop request."""
    clock = clock or RealClock()
    ledger = ledger if ledger is not None else Ledger()
    result = WatchResult(reason="", state=session.state)
    previous: Snapshot | None = None
    backoff = 0

    if directory is not None:
        audit.append(directory, "watch_started", watch_mode=session.watch_mode,
                     deadline=session.watch_deadline, provider=session.provider)

    while True:
        if stop is not None and stop():
            return _finish(result, STOPPED, session, directory, state_root)

        try:
            current = collect()
            backoff = 0
        except SnapshotError as error:
            result.errors += 1
            if directory is not None:
                audit.append(directory, "poll_failed", error=str(error),
                             retryable=error.retryable)
            if not error.retryable:
                raise
            backoff = min(POLL_BACKOFF_MAX, max(POLL_ACTIVE, backoff * 2 or POLL_ACTIVE))
            if not _sleep_until(session, clock, backoff):
                return _finish(result, states.WATCH_TIMEOUT, session, directory, state_root)
            continue

        result.polls += 1
        result.last_snapshot = current
        delta = compare(previous, current, own_comment_ids=own_comment_ids,
                        own_head_shas=own_head_shas)

        if delta.meaningful and not current.partial:
            findings_module.apply_snapshot(ledger, current)
            if delta.deleted_comments:
                findings_module.apply_withdrawals(ledger, delta.deleted_comments)
            if directory is not None:
                write_json(directory / "snapshot.json", current.to_dict())
                findings_module.save(directory, ledger)
                audit.append(directory, "delta", summary=delta.summary(),
                             wake_reason=delta.wake_reason,
                             sleep_reason=delta.sleep_reason)
            session.head_sha = current.head_sha
            session.last_snapshot_digest = current.digest()
            session.last_ci_state = current.ci_state
        elif current.partial and directory is not None:
            # An incomplete collection must never become the baseline: the next
            # complete one would read the missing sections as new activity.
            audit.append(directory, "snapshot_partial", reasons=current.partial_reasons)

        session.state = states.classify(current, ledger)
        result.state = session.state
        result.history.append(session.state)

        if delta.should_wake and wake is not None:
            session.state = states.WORKING
            _persist(session, directory, state_root)
            if directory is not None:
                audit.append(directory, "ai_wake", reason=delta.wake_reason,
                             summary=delta.summary())
            wake(current, delta)
            session.wake_count += 1
            result.wakes += 1
            # The AI has exited; re-classify from what it left behind.
            session.state = states.classify(current, ledger)
            result.state = session.state

        _persist(session, directory, state_root)

        if states.is_terminal(session.state):
            return _finish(result, session.state, session, directory, state_root)
        if session.watch_mode == "single":
            result.reason = SINGLE_PASS
            return _finish(result, SINGLE_PASS, session, directory, state_root)

        interval = poll_interval(session.state,
                                 recently_active=delta.head_changed or delta.ci_changed)
        if not _sleep_until(session, clock, interval):
            return _finish(result, states.WATCH_TIMEOUT, session, directory, state_root)
        if not current.partial:
            previous = current


def _sleep_until(session: Session, clock: Clock, interval: int) -> bool:
    """Sleep for `interval`, or until the watch deadline. False => deadline reached."""
    if session.watch_mode == "until-close":
        clock.sleep(interval)
        return True
    deadline = session.watch_deadline
    if deadline is None:
        clock.sleep(interval)
        return True
    remaining = deadline - clock.now()
    if remaining <= 0:
        return False
    clock.sleep(min(interval, remaining))
    return clock.now() < deadline


def _persist(session: Session, directory: Path | None, state_root: Path | None) -> None:
    if directory is None:
        return
    save_session(session, root=state_root)


def _finish(result: WatchResult, reason: str, session: Session, directory: Path | None,
            state_root: Path | None) -> WatchResult:
    result.reason = reason
    if reason in states.TERMINAL:
        session.state = reason
        session.terminal_reason = reason
    result.state = session.state
    _persist(session, directory, state_root)
    if directory is not None:
        audit.append(directory, "watch_finished", reason=reason, state=session.state,
                     polls=result.polls, wakes=result.wakes)
    return result
