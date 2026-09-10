"""The PR lifecycle state machine (§9).

READY is NOT terminal: it means only that the current HEAD looks good, required
CI passed, known findings are settled, and no change is requested. A reviewer may
still comment an hour later, which is exactly why coder-ai-os keeps watching.
"""

from __future__ import annotations

from coderai.pr_automation.findings import CONFLICTED, HUMAN_REQUIRED, Ledger
from coderai.pr_automation.snapshot import Snapshot

OPEN = "OPEN"
READY = "READY"
WAITING_FOR_CI = "WAITING_FOR_CI"
WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
HUMAN_NEEDED = "HUMAN_NEEDED"
WORKING = "WORKING"

MERGED = "MERGED"
CLOSED = "CLOSED"
WATCH_TIMEOUT = "WATCH_TIMEOUT"
USER_STOP = "USER_STOP"

TERMINAL = frozenset({MERGED, CLOSED, WATCH_TIMEOUT, USER_STOP})
NON_TERMINAL = frozenset({OPEN, READY, WAITING_FOR_CI, WAITING_FOR_REVIEW,
                          HUMAN_NEEDED, WORKING})


def is_terminal(state: str) -> bool:
    return state in TERMINAL


def classify(snapshot: Snapshot, ledger: Ledger | None = None) -> str:
    """The PR's current state, from evidence alone — no interpretation."""
    if snapshot.state == "MERGED":
        return MERGED
    if snapshot.state == "CLOSED":
        return CLOSED

    findings = ledger.findings.values() if ledger else []
    if any(item.state in {HUMAN_REQUIRED, CONFLICTED} for item in findings):
        return HUMAN_NEEDED

    ci = snapshot.ci_state
    if ci == "failed":
        return OPEN
    if ci == "pending":
        return WAITING_FOR_CI

    if ledger and ledger.open_findings():
        return OPEN
    if any(review.state == "CHANGES_REQUESTED" for review in snapshot.reviews):
        # A change request that no longer maps to an open finding still blocks READY
        # until a reviewer approves or dismisses it.
        if not any(review.state == "APPROVED" for review in snapshot.reviews):
            return OPEN

    if any(review.state == "APPROVED" for review in snapshot.reviews):
        return READY
    return WAITING_FOR_REVIEW
