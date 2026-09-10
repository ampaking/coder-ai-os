"""Verify, afterwards, that only the PR head moved — and that it moved forward.

The AI owns the commit decision and runs `git push` itself (§30, §43). coder-ai-os's job
is proof (§14): capture the remote's refs before the run, compare them after, and
assert that exactly one ref changed, as a fast-forward, with no tags created.

On a mismatch coder-ai-os stops mutating and escalates. It never "fixes" a bad push with
another push — that is how history gets overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from coderai.pr_automation import audit
from coderai.pr_automation.gitcmd import Git, GitError

VERIFIED = "VERIFIED"
NO_CHANGE = "NO_CHANGE"
UNPUSHED_COMMITS = "UNPUSHED_COMMITS"
NON_FAST_FORWARD = "NON_FAST_FORWARD"
UNEXPECTED_REF = "UNEXPECTED_REF"
TAG_CREATED = "TAG_CREATED"
UNREADABLE = "UNREADABLE"

ESCALATE = {NON_FAST_FORWARD, UNEXPECTED_REF, TAG_CREATED}


@dataclass
class PushVerification:
    status: str
    head_ref: str = ""
    before_sha: str = ""
    after_sha: str = ""
    moved_refs: list[str] = field(default_factory=list)
    unpushed: int = 0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {VERIFIED, NO_CHANGE}

    @property
    def needs_human(self) -> bool:
        return self.status in ESCALATE

    @property
    def pushed(self) -> bool:
        return self.status == VERIFIED


def capture_refs(git: Git, remote: str) -> dict[str, str]:
    """Every ref the remote currently publishes: ref -> sha."""
    try:
        result = git.check("ls-remote", remote, timeout=120)
    except GitError:
        return {}
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            refs[parts[1]] = parts[0]
    return refs


def unpushed_commits(git: Git, remote: str, head_branch: str) -> int:
    """Local commits the PR head does not have yet."""
    value = git.maybe("rev-list", "--count", f"{remote}/{head_branch}..HEAD")
    try:
        return int(value or 0)
    except ValueError:
        return 0


def verify(git: Git, *, remote: str, head_branch: str, before: dict[str, str],
           after: dict[str, str] | None = None,
           directory: Path | None = None) -> PushVerification:
    """Compare the remote before and after an AI run. Proof, not trust."""
    after = capture_refs(git, remote) if after is None else after
    head_ref = f"refs/heads/{head_branch}"
    if not after:
        return PushVerification(UNREADABLE, head_ref=head_ref,
                                reason="could not read the remote's refs")

    moved = sorted(ref for ref in set(before) | set(after)
                   if before.get(ref) != after.get(ref))
    before_sha = before.get(head_ref, "")
    after_sha = after.get(head_ref, "")

    tags = [ref for ref in moved if ref.startswith("refs/tags/")]
    if tags:
        result = PushVerification(TAG_CREATED, head_ref=head_ref, before_sha=before_sha,
                                  after_sha=after_sha, moved_refs=moved,
                                  reason=f"tags changed during the run: {', '.join(tags)}")
        return _record(result, directory)

    others = [ref for ref in moved if ref != head_ref]
    if others:
        result = PushVerification(UNEXPECTED_REF, head_ref=head_ref, before_sha=before_sha,
                                  after_sha=after_sha, moved_refs=moved,
                                  reason=f"refs outside this PR changed: {', '.join(others)}")
        return _record(result, directory)

    outstanding = unpushed_commits(git, remote, head_branch)

    if head_ref not in moved:
        status = UNPUSHED_COMMITS if outstanding else NO_CHANGE
        reason = (f"{outstanding} local commit(s) were never pushed" if outstanding else "")
        return _record(PushVerification(status, head_ref=head_ref, before_sha=before_sha,
                                        after_sha=after_sha, unpushed=outstanding,
                                        reason=reason), directory)

    if before_sha and not git.is_ancestor(before_sha, after_sha):
        result = PushVerification(NON_FAST_FORWARD, head_ref=head_ref, before_sha=before_sha,
                                  after_sha=after_sha, moved_refs=moved,
                                  reason="the PR head did not fast-forward; history may have "
                                         "been rewritten or a human pushed concurrently")
        return _record(result, directory)

    return _record(PushVerification(VERIFIED, head_ref=head_ref, before_sha=before_sha,
                                    after_sha=after_sha, moved_refs=moved,
                                    unpushed=outstanding), directory)


def _record(result: PushVerification, directory: Path | None) -> PushVerification:
    if directory is None:
        return result
    if result.status == VERIFIED:
        audit.append(directory, "push_complete", commit=result.after_sha,
                     ref=result.head_ref, previous=result.before_sha)
    elif result.status != NO_CHANGE:
        audit.append(directory, "push_verification_failed", status=result.status,
                     ref=result.head_ref, moved=result.moved_refs, reason=result.reason)
    return result


@dataclass
class PushTransaction:
    """Bracket an AI run so its git effects can be proven afterwards."""

    git: Git
    remote: str
    head_branch: str
    directory: Path | None = None
    before: dict[str, str] = field(default_factory=dict)

    def open(self) -> "PushTransaction":
        self.before = capture_refs(self.git, self.remote)
        return self

    def close(self) -> PushVerification:
        return verify(self.git, remote=self.remote, head_branch=self.head_branch,
                      before=self.before, directory=self.directory)

    def __enter__(self) -> "PushTransaction":
        return self.open()

    def __exit__(self, *exc) -> None:
        return None
