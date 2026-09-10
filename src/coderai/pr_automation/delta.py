"""What changed between two snapshots, and whether it needs engineering judgment.

§48–49: coder-ai-os does simple mechanical filtering only. Five comments plus one
submitted review arriving in the same poll produce ONE triage, not six wakes —
because a delta is computed per snapshot transition, and it carries at most one
wake reason.

Nothing here interprets review *content*. "Is this concern valid?" is the AI's
job; "did anything happen at all?" is this module's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from coderai.pr_automation.snapshot import Snapshot

# wake reasons, most urgent first — the order is the priority order
TERMINAL = "TERMINAL"
NEW_HEAD = "NEW_HEAD"
CI_FAILED = "CI_FAILED"
NEW_DISCUSSION = "NEW_DISCUSSION"
FIRST_SNAPSHOT = "FIRST_SNAPSHOT"

# sleep reasons
NO_CHANGE = "NO_CHANGE"
OWN_UPDATE_ONLY = "OWN_UPDATE_ONLY"
RESOLUTION_ONLY = "RESOLUTION_ONLY"
CI_PROGRESS_ONLY = "CI_PROGRESS_ONLY"
COSMETIC_ONLY = "COSMETIC_ONLY"
PARTIAL_SNAPSHOT = "PARTIAL_SNAPSHOT"

_WHITESPACE = re.compile(r"\s+")


def _normalize(body: str) -> str:
    return _WHITESPACE.sub(" ", body or "").strip()


@dataclass
class Delta:
    """A mechanical description of one snapshot transition."""

    new_comments: list[str] = field(default_factory=list)
    edited_comments: list[str] = field(default_factory=list)
    deleted_comments: list[str] = field(default_factory=list)
    new_reviews: list[str] = field(default_factory=list)
    new_review_comments: list[str] = field(default_factory=list)
    edited_review_comments: list[str] = field(default_factory=list)
    resolved_threads: list[str] = field(default_factory=list)
    outdated_threads: list[str] = field(default_factory=list)

    head_changed: bool = False
    head_from: str = ""
    head_to: str = ""
    ci_changed: bool = False
    ci_from: str = ""
    ci_to: str = ""
    state_changed: bool = False
    state_from: str = ""
    state_to: str = ""

    terminal: bool = False
    meaningful: bool = False          # something changed worth persisting
    wake_reason: str = ""             # non-empty => wake the AI
    sleep_reason: str = ""            # why no wake is needed

    @property
    def should_wake(self) -> bool:
        return bool(self.wake_reason)

    def summary(self) -> str:
        parts = []
        if self.new_comments:
            parts.append(f"{len(self.new_comments)} new comment(s)")
        if self.new_reviews:
            parts.append(f"{len(self.new_reviews)} new review(s)")
        if self.new_review_comments:
            parts.append(f"{len(self.new_review_comments)} new inline comment(s)")
        if self.edited_comments or self.edited_review_comments:
            parts.append(f"{len(self.edited_comments) + len(self.edited_review_comments)} edit(s)")
        if self.deleted_comments:
            parts.append(f"{len(self.deleted_comments)} deletion(s)")
        if self.resolved_threads:
            parts.append(f"{len(self.resolved_threads)} thread(s) resolved")
        if self.head_changed:
            parts.append(f"HEAD {self.head_from[:7]}→{self.head_to[:7]}")
        if self.ci_changed:
            parts.append(f"CI {self.ci_from}→{self.ci_to}")
        if self.state_changed:
            parts.append(f"PR {self.state_from}→{self.state_to}")
        return ", ".join(parts) or "no change"


def _index(items) -> dict[str, object]:
    return {item.id: item for item in items if getattr(item, "id", "")}


def compare(previous: Snapshot | None, current: Snapshot, *,
            own_comment_ids: set[str] | None = None,
            own_head_shas: set[str] | None = None) -> Delta:
    """Compare two snapshots. Pure: no network, no model, no filesystem."""
    own_comments = own_comment_ids or set()
    own_heads = own_head_shas or set()

    if previous is None:
        delta = Delta(meaningful=True, wake_reason=FIRST_SNAPSHOT,
                      head_to=current.head_sha, state_to=current.state,
                      ci_to=current.ci_state)
        if current.is_terminal:
            delta.terminal = True
            delta.wake_reason = ""
            delta.sleep_reason = TERMINAL
        return delta

    delta = Delta()

    before_comments = _index(previous.comments)
    after_comments = _index(current.comments)
    for identifier, item in after_comments.items():
        prior = before_comments.get(identifier)
        if prior is None:
            delta.new_comments.append(identifier)
        elif _normalize(prior.body) != _normalize(item.body):
            delta.edited_comments.append(identifier)
    delta.deleted_comments = sorted(set(before_comments) - set(after_comments))

    before_reviews = _index(previous.reviews)
    delta.new_reviews = sorted(set(_index(current.reviews)) - set(before_reviews))

    before_inline = _index(previous.review_comments)
    after_inline = _index(current.review_comments)
    for identifier, item in after_inline.items():
        prior = before_inline.get(identifier)
        if prior is None:
            delta.new_review_comments.append(identifier)
        elif _normalize(prior.body) != _normalize(item.body):
            delta.edited_review_comments.append(identifier)

    before_threads = {item.thread_id: item for item in previous.review_comments}
    for item in current.review_comments:
        prior = before_threads.get(item.thread_id)
        if item.resolved and (prior is None or not prior.resolved):
            if item.thread_id not in delta.resolved_threads:
                delta.resolved_threads.append(item.thread_id)
        if item.outdated and (prior is None or not prior.outdated):
            if item.thread_id not in delta.outdated_threads:
                delta.outdated_threads.append(item.thread_id)

    delta.head_from, delta.head_to = previous.head_sha, current.head_sha
    delta.head_changed = previous.head_sha != current.head_sha
    delta.ci_from, delta.ci_to = previous.ci_state, current.ci_state
    delta.ci_changed = previous.ci_state != current.ci_state
    delta.state_from, delta.state_to = previous.state, current.state
    delta.state_changed = previous.state != current.state
    delta.terminal = current.is_terminal

    _decide(delta, previous, current, own_comments, own_heads)
    return delta


def _decide(delta: Delta, previous: Snapshot, current: Snapshot,
            own_comments: set[str], own_heads: set[str]) -> None:
    """Mechanical wake/sleep decision — one reason, always named."""
    if current.is_terminal:
        delta.meaningful = True
        delta.sleep_reason = TERMINAL
        return

    if previous.digest() == current.digest():
        delta.sleep_reason = NO_CHANGE
        return

    delta.meaningful = True

    if current.partial:
        # An incomplete collection must never be read as "things disappeared".
        delta.sleep_reason = PARTIAL_SNAPSHOT
        return

    foreign_comments = [item for item in delta.new_comments if item not in own_comments]
    foreign_edits = [item for item in delta.edited_comments if item not in own_comments]
    foreign_inline = [item for item in delta.new_review_comments if item not in own_comments]
    discussion = bool(foreign_comments or foreign_edits or foreign_inline
                      or delta.new_reviews or delta.edited_review_comments
                      or delta.deleted_comments)

    if delta.head_changed and delta.head_to not in own_heads:
        delta.wake_reason = NEW_HEAD
        return
    if delta.ci_changed and delta.ci_to == "failed":
        delta.wake_reason = CI_FAILED
        return
    if discussion:
        delta.wake_reason = NEW_DISCUSSION
        return

    # Everything that changed was mechanical: record it, stay asleep.
    if delta.new_comments or delta.edited_comments:
        delta.sleep_reason = OWN_UPDATE_ONLY
    elif delta.resolved_threads or delta.outdated_threads:
        delta.sleep_reason = RESOLUTION_ONLY
    elif delta.ci_changed or delta.head_changed:
        delta.sleep_reason = CI_PROGRESS_ONLY
    else:
        delta.sleep_reason = COSMETIC_ONLY


def is_stale(running_head: str, current: Snapshot) -> bool:
    """§53: a human pushed while the AI was working — the run is stale."""
    return bool(running_head) and bool(current.head_sha) and running_head != current.head_sha
