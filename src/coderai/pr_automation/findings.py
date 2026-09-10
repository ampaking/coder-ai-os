"""The durable findings ledger (§50–52).

Without it, the same concern is re-analyzed on every wake, and a reviewer's
"確認しました。このままで大丈夫です。" would create a new finding instead of
closing the old one.

coder-ai-os owns only the mechanical transitions — a thread got resolved, its code
disappeared, the comment was withdrawn. Judgment transitions (VERIFIED,
REJECTED, CONFLICTED) are written from an AI decision, never inferred here.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from coderai.pr_automation.snapshot import Snapshot
from coderai.pr_automation.state import read_json, write_json

SCHEMA_VERSION = 1
FINDINGS_FILE = "findings.json"

OPEN = "OPEN"
VERIFYING = "VERIFYING"
VERIFIED = "VERIFIED"
REJECTED = "REJECTED"
FIXING = "FIXING"
FIXED = "FIXED"
RESOLVED = "RESOLVED"
OUTDATED = "OUTDATED"
CONFLICTED = "CONFLICTED"
HUMAN_REQUIRED = "HUMAN_REQUIRED"

STATES = (OPEN, VERIFYING, VERIFIED, REJECTED, FIXING, FIXED, RESOLVED, OUTDATED,
          CONFLICTED, HUMAN_REQUIRED)
# States that no longer need AI attention.
SETTLED = {REJECTED, FIXED, RESOLVED, OUTDATED}
# A crashed run must not leave work permanently "in progress".
IN_FLIGHT = {VERIFYING, FIXING}

KIND_HUMAN_REVIEW = "human_review"
KIND_AI_REVIEW = "ai_review"
KIND_CHANGE_REQUEST = "change_request"
KIND_CI = "ci"
KIND_OTHER = "other"

MAX_EXCERPT = 2000
MAX_HISTORY = 50


class FindingsError(RuntimeError):
    """The ledger cannot be read or transitioned safely."""


def finding_id(anchor: str) -> str:
    """Stable across snapshots: derived from thread/comment identity, not position."""
    return "F-" + hashlib.sha256(anchor.encode("utf-8")).hexdigest()[:8]


@dataclass
class Finding:
    id: str
    state: str
    kind: str
    author: str = ""
    is_bot: bool = False
    thread_id: str = ""
    comment_id: str = ""
    path: str = ""
    line: int | None = None
    excerpt: str = ""
    created_at: str = ""
    updated_at: str = ""
    note: str = ""
    resolved_by: str = ""
    self_resolved: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def settled(self) -> bool:
        return self.state in SETTLED

    @property
    def needs_attention(self) -> bool:
        return self.state in {OPEN, VERIFIED, CONFLICTED}

    def anchor(self) -> str:
        location = f"{self.path}:{self.line}" if self.path else ""
        return location or self.thread_id or self.comment_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Ledger:
    findings: dict[str, Finding] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __len__(self) -> int:
        return len(self.findings)

    def get(self, identifier: str) -> Finding | None:
        return self.findings.get(identifier)

    def open_findings(self) -> list[Finding]:
        return [item for item in self.findings.values() if item.needs_attention]

    def in_flight(self) -> list[Finding]:
        return [item for item in self.findings.values() if item.state in IN_FLIGHT]

    def by_thread(self, thread_id: str) -> Finding | None:
        for item in self.findings.values():
            if item.thread_id and item.thread_id == thread_id:
                return item
        return None

    def transition(self, identifier: str, state: str, *, by: str = "coder-ai",
                   note: str = "", now: float | None = None) -> Finding:
        finding = self.findings.get(identifier)
        if finding is None:
            raise FindingsError(f"unknown finding: {identifier}")
        if state not in STATES:
            raise FindingsError(f"unknown finding state: {state}")
        if finding.state == state:
            return finding
        finding.history.append({
            "at": round(time.time() if now is None else now, 3),
            "from": finding.state, "to": state, "by": by, "note": note[:400],
        })
        finding.history = finding.history[-MAX_HISTORY:]
        finding.state = state
        if note:
            finding.note = note[:400]
        return finding

    def upsert(self, finding: Finding) -> Finding:
        existing = self.findings.get(finding.id)
        if existing is None:
            self.findings[finding.id] = finding
            return finding
        # Never resurrect a settled finding from the same evidence (§51).
        existing.excerpt = finding.excerpt or existing.excerpt
        existing.path = finding.path or existing.path
        existing.line = finding.line if finding.line is not None else existing.line
        existing.updated_at = finding.updated_at or existing.updated_at
        return existing

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version,
                "findings": {key: item.to_dict() for key, item in self.findings.items()}}

    @classmethod
    def from_dict(cls, value: Any) -> "Ledger":
        if not isinstance(value, dict):
            return cls()
        version = int(value.get("schema_version", SCHEMA_VERSION))
        if version > SCHEMA_VERSION:
            raise FindingsError(f"findings schema {version} is newer than {SCHEMA_VERSION}")
        findings: dict[str, Finding] = {}
        for key, item in (value.get("findings") or {}).items():
            if not isinstance(item, dict):
                continue
            known = {name: item[name] for name in Finding.__dataclass_fields__ if name in item}
            findings[str(key)] = Finding(**known)
        return cls(findings=findings, schema_version=version)

    def view(self, limit: int = 40) -> str:
        """A compact ledger for the triage prompt — one line per finding."""
        rows = sorted(self.findings.values(),
                      key=lambda item: (item.settled, item.id))[:limit]
        lines = []
        for item in rows:
            where = item.anchor()
            mark = ""
            if item.state == RESOLVED and item.resolved_by:
                mark = (f" [resolved by {item.resolved_by}"
                        f"{' — the PR author' if item.self_resolved else ''}]")
            excerpt = item.excerpt[:60].splitlines()[0] if item.excerpt else ""
            lines.append(f"{item.id}  {item.state:<14} {item.kind:<15} "
                         f"{where[:40]:<40} {excerpt}{mark}")
        return "\n".join(lines)


# ---------------------------------------------------------------- persistence


def load(directory: Path) -> Ledger:
    value = read_json(Path(directory) / FINDINGS_FILE)
    return Ledger.from_dict(value) if value is not None else Ledger()


def save(directory: Path, ledger: Ledger) -> None:
    write_json(Path(directory) / FINDINGS_FILE, ledger.to_dict())


# ---------------------------------------------------------------- mechanical


def _kind(author_is_bot: bool) -> str:
    return KIND_AI_REVIEW if author_is_bot else KIND_HUMAN_REVIEW


def apply_snapshot(ledger: Ledger, snapshot: Snapshot, *,
                   own_authors: Iterable[str] = (), now: float | None = None) -> Ledger:
    """Create and settle findings from what the PR itself states — no judgment.

    Only unambiguous evidence creates a finding: an inline review thread, or a
    submitted CHANGES_REQUESTED review. Everything else is for the AI to promote
    explicitly, so coder-ai-os never invents work.

    Resolution is read exactly as GitHub reports it — resolved, unresolved again,
    outdated, or gone. Who resolved a thread is recorded but never judged here: a
    PR author resolving their own thread is not the same evidence as the reviewer
    doing it, and that distinction is the AI's to weigh.
    """
    mine = {name.lower() for name in own_authors}
    author = (snapshot.author or "").lower()

    seen_threads: set[str] = set()
    for comment in snapshot.review_comments:
        if comment.author.lower() in mine:
            continue
        if comment.thread_id in seen_threads:
            continue
        seen_threads.add(comment.thread_id)
        identifier = finding_id(comment.thread_id or comment.id)
        existing = ledger.get(identifier)
        if existing is None:
            ledger.upsert(Finding(
                id=identifier, state=OPEN, kind=_kind(comment.is_bot),
                author=comment.author, is_bot=comment.is_bot,
                thread_id=comment.thread_id, comment_id=comment.id,
                path=comment.path, line=comment.line,
                excerpt=comment.body[:MAX_EXCERPT],
                created_at=comment.created_at, updated_at=comment.updated_at,
            ))
            existing = ledger.get(identifier)
        assert existing is not None
        if comment.resolved:
            existing.resolved_by = comment.resolved_by
            existing.self_resolved = bool(
                comment.resolved_by and comment.resolved_by.lower() == author)
            if existing.state != RESOLVED:
                who = comment.resolved_by or "someone"
                note = f"review thread resolved by {who}"
                if existing.self_resolved:
                    note += " (the PR author — not reviewer confirmation)"
                ledger.transition(identifier, RESOLVED, by="github", note=note, now=now)
        elif existing.state == RESOLVED:
            # GitHub allows re-opening a conversation: the concern is live again.
            existing.resolved_by = ""
            existing.self_resolved = False
            ledger.transition(identifier, OPEN, by="github",
                              note="review thread re-opened", now=now)
        elif comment.outdated and existing.state not in {OUTDATED, RESOLVED, FIXED}:
            ledger.transition(identifier, OUTDATED, by="github",
                              note="anchored code no longer present", now=now)

    for review in snapshot.reviews:
        if review.state != "CHANGES_REQUESTED" or review.author.lower() in mine:
            continue
        identifier = finding_id(f"review:{review.id}")
        if ledger.get(identifier) is None:
            ledger.upsert(Finding(
                id=identifier, state=OPEN, kind=KIND_CHANGE_REQUEST,
                author=review.author, is_bot=review.is_bot, comment_id=review.id,
                excerpt=(review.body or "changes requested")[:MAX_EXCERPT],
                created_at=review.submitted_at, updated_at=review.submitted_at,
            ))

    _settle_superseded_change_requests(ledger, snapshot, now=now)
    _settle_vanished_threads(ledger, snapshot, now=now)
    return ledger


def _settle_superseded_change_requests(ledger: Ledger, snapshot: Snapshot,
                                       now: float | None = None) -> list[str]:
    """A reviewer who later approves (or has their review dismissed) has withdrawn it.

    Without this, a CHANGES_REQUESTED finding stays OPEN forever — the PR reads as
    blocked long after the reviewer said yes.
    """
    settled: list[str] = []
    latest: dict[str, tuple[str, str]] = {}
    for review in snapshot.reviews:
        key = review.author.lower()
        stamp = review.submitted_at or ""
        if key not in latest or stamp >= latest[key][0]:
            latest[key] = (stamp, review.state)

    for review in snapshot.reviews:
        if review.state != "CHANGES_REQUESTED":
            continue
        identifier = finding_id(f"review:{review.id}")
        finding = ledger.get(identifier)
        if finding is None or finding.settled:
            continue
        newest_stamp, newest_state = latest.get(review.author.lower(), ("", ""))
        superseded = (newest_state in {"APPROVED", "DISMISSED"}
                      and newest_stamp >= (review.submitted_at or ""))
        if superseded:
            finding.resolved_by = review.author
            ledger.transition(identifier, RESOLVED, by="github",
                              note=f"{review.author} later submitted {newest_state}",
                              now=now)
            settled.append(identifier)
    return settled


def _settle_vanished_threads(ledger: Ledger, snapshot: Snapshot,
                             now: float | None = None) -> list[str]:
    """A deleted review comment takes its thread with it — the concern is withdrawn."""
    if snapshot.partial:
        return []  # an incomplete collection must never read as "deleted"
    present = {item.thread_id for item in snapshot.review_comments if item.thread_id}
    settled: list[str] = []
    for finding in list(ledger.findings.values()):
        if not finding.thread_id or finding.settled:
            continue
        if finding.thread_id not in present:
            ledger.transition(finding.id, RESOLVED, by="github",
                              note="review thread was deleted", now=now)
            settled.append(finding.id)
    return settled


def apply_withdrawals(ledger: Ledger, deleted_comment_ids: Iterable[str],
                      now: float | None = None) -> list[str]:
    """A reviewer withdrew a comment: the finding it created is resolved (§52)."""
    settled: list[str] = []
    deleted = set(deleted_comment_ids)
    for finding in list(ledger.findings.values()):
        if finding.comment_id in deleted and not finding.settled:
            ledger.transition(finding.id, RESOLVED, by="github",
                              note="comment withdrawn", now=now)
            settled.append(finding.id)
    return settled


def recover_in_flight(ledger: Ledger, now: float | None = None) -> list[str]:
    """A crashed run must not leave findings stuck in VERIFYING/FIXING (§51)."""
    recovered: list[str] = []
    for finding in list(ledger.findings.values()):
        if finding.state in IN_FLIGHT:
            ledger.transition(finding.id, OPEN, by="coder-ai",
                              note=f"recovered from interrupted {finding.state.lower()}",
                              now=now)
            recovered.append(finding.id)
    return recovered


def apply_decision(ledger: Ledger, updates: Iterable[dict[str, Any]], *,
                   by: str = "ai", now: float | None = None) -> list[str]:
    """Write the AI's judgment transitions. Unknown ids are created as OPEN first."""
    changed: list[str] = []
    for update in updates:
        if not isinstance(update, dict):
            continue
        identifier = str(update.get("id") or "").strip()
        state = str(update.get("state") or "").strip().upper()
        if not identifier or state not in STATES:
            continue
        if ledger.get(identifier) is None:
            ledger.upsert(Finding(
                id=identifier, state=OPEN, kind=str(update.get("kind") or KIND_OTHER),
                excerpt=str(update.get("note") or "")[:MAX_EXCERPT],
                path=str(update.get("path") or ""), author=by,
            ))
        ledger.transition(identifier, state, by=by, note=str(update.get("note") or ""),
                          now=now)
        changed.append(identifier)
    return changed
