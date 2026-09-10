"""Canonical PR state (§46, §47). coder-ai-os gathers; the AI interprets.

Deliberately does NOT fetch the diff: the agent has the isolated worktree, so
pulling a megabyte of patch through the API and into a prompt would be waste.
Changed-file names are collected; the content is read from disk when needed.

Every collection is bounded and marked when truncated, so the AI can tell
"nothing there" from "capped at 200".
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from coderai.pr_automation.github import Gh, GhError

SCHEMA_VERSION = 1

MAX_COMMENTS = 200
MAX_REVIEWS = 100
MAX_REVIEW_COMMENTS = 300
MAX_COMMITS = 100
MAX_FILES = 300
MAX_CHECKS = 100
MAX_BODY = 20_000

PR_VIEW_FIELDS = (
    "number,title,body,author,state,isDraft,url,headRefName,baseRefName,headRefOid,"
    "mergeable,isCrossRepository,createdAt,updatedAt,comments,reviews,commits,files,"
    "statusCheckRollup,latestReviews,labels,reviewRequests,mergedAt,closedAt"
)

# GraphQL is the only source for thread identity and resolution state.
THREADS_QUERY = """
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100) {
        nodes {
          id isResolved isOutdated isCollapsed path line originalLine
          resolvedBy { login }
          comments(first:50) {
            nodes {
              id databaseId body createdAt updatedAt url
              author { login __typename }
              path originalLine line outdated
            }
          }
        }
      }
    }
  }
}
"""

_ISSUE_REF = re.compile(r"(?:^|[\s(\[])#(\d{1,7})\b")
_ISSUE_URL = re.compile(r"https?://[^/\s]+/([\w.-]+)/([\w.-]+)/issues/(\d{1,7})")


class SnapshotError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _clip(value: Any, limit: int = MAX_BODY) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _login(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("login") or value.get("name") or "")
    return str(value or "")


def _is_bot(author: Any) -> bool:
    if isinstance(author, dict):
        if str(author.get("__typename") or author.get("type") or "").lower() == "bot":
            return True
        if bool(author.get("is_bot")):
            return True
    login = _login(author)
    return login.endswith("[bot]") or login in {"github-actions", "dependabot"}


@dataclass
class Comment:
    id: str
    author: str
    body: str
    created_at: str = ""
    updated_at: str = ""
    is_bot: bool = False
    kind: str = "issue"          # issue | review_body
    url: str = ""


@dataclass
class Review:
    id: str
    author: str
    state: str                   # APPROVED | CHANGES_REQUESTED | COMMENTED | DISMISSED
    body: str = ""
    submitted_at: str = ""
    is_bot: bool = False


@dataclass
class ReviewComment:
    id: str
    thread_id: str
    author: str
    body: str
    path: str = ""
    line: int | None = None
    resolved: bool = False
    resolved_by: str = ""
    outdated: bool = False
    created_at: str = ""
    updated_at: str = ""
    is_bot: bool = False
    url: str = ""


@dataclass
class Check:
    name: str
    status: str                  # QUEUED | IN_PROGRESS | COMPLETED
    conclusion: str = ""         # SUCCESS | FAILURE | CANCELLED | SKIPPED | NEUTRAL
    workflow: str = ""
    url: str = ""
    required: bool = False

    @property
    def failed(self) -> bool:
        return self.conclusion in {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}

    @property
    def pending(self) -> bool:
        return self.status in {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING"}


@dataclass
class Commit:
    sha: str
    message: str = ""
    author: str = ""
    at: str = ""


@dataclass
class Snapshot:
    number: int
    state: str
    head_sha: str
    title: str = ""
    body: str = ""
    author: str = ""
    head_branch: str = ""
    base_branch: str = ""
    is_draft: bool = False
    mergeable: str = ""
    url: str = ""
    updated_at: str = ""

    comments: list[Comment] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    review_comments: list[ReviewComment] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    linked_issues: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    truncated: list[str] = field(default_factory=list)
    partial: bool = False
    partial_reasons: list[str] = field(default_factory=list)
    collected_at: float = 0.0
    schema_version: int = SCHEMA_VERSION

    # ---- derived views -------------------------------------------------
    @property
    def ci_state(self) -> str:
        if not self.checks:
            return "none"
        if any(check.failed for check in self.checks):
            return "failed"
        if any(check.pending for check in self.checks):
            return "pending"
        return "passed"

    @property
    def failed_checks(self) -> list[Check]:
        return [check for check in self.checks if check.failed]

    @property
    def open_threads(self) -> list[ReviewComment]:
        return [item for item in self.review_comments if not item.resolved and not item.outdated]

    @property
    def is_terminal(self) -> bool:
        return self.state in {"MERGED", "CLOSED"}

    def digest(self) -> str:
        """Stable content fingerprint: identical PR state → identical digest."""
        payload = {
            "state": self.state, "head_sha": self.head_sha, "title": self.title,
            "body": self.body, "is_draft": self.is_draft, "mergeable": self.mergeable,
            "labels": sorted(self.labels),
            "comments": sorted((item.id, item.updated_at or item.created_at,
                                hashlib.sha256(item.body.encode()).hexdigest()[:16])
                               for item in self.comments),
            "reviews": sorted((item.id, item.state, item.submitted_at,
                               hashlib.sha256(item.body.encode()).hexdigest()[:16])
                              for item in self.reviews),
            "review_comments": sorted(
                (item.id, item.updated_at or item.created_at, item.resolved,
                 item.resolved_by, item.outdated,
                 hashlib.sha256(item.body.encode()).hexdigest()[:16])
                for item in self.review_comments),
            "checks": sorted((item.name, item.status, item.conclusion) for item in self.checks),
            "commits": [item.sha for item in self.commits],
            "files": sorted(self.changed_files),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["digest"] = self.digest()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Snapshot":
        version = int(value.get("schema_version", 0))
        if version > SCHEMA_VERSION:
            raise SnapshotError(f"snapshot schema {version} is newer than {SCHEMA_VERSION}")
        payload = dict(value)
        payload.pop("digest", None)
        known = set(cls.__dataclass_fields__)
        payload = {key: item for key, item in payload.items() if key in known}
        payload["comments"] = [Comment(**item) for item in payload.get("comments", [])]
        payload["reviews"] = [Review(**item) for item in payload.get("reviews", [])]
        payload["review_comments"] = [ReviewComment(**item)
                                      for item in payload.get("review_comments", [])]
        payload["checks"] = [Check(**item) for item in payload.get("checks", [])]
        payload["commits"] = [Commit(**item) for item in payload.get("commits", [])]
        return cls(**payload)


def _cap(items: list, limit: int, name: str, truncated: list[str]) -> list:
    if len(items) > limit:
        truncated.append(f"{name}:{len(items)}>{limit}")
        return items[-limit:] if name in {"comments", "commits"} else items[:limit]
    return items


def _linked_issues(texts: Iterable[str], owner: str, repo: str) -> list[str]:
    """Reference extraction is deterministic and local — no API call needed."""
    found: set[str] = set()
    for text in texts:
        for match in _ISSUE_REF.finditer(text or ""):
            found.add(f"{owner}/{repo}#{match.group(1)}")
        for match in _ISSUE_URL.finditer(text or ""):
            found.add(f"{match.group(1)}/{match.group(2)}#{match.group(3)}")
    return sorted(found)[:50]


def _parse_checks(rollup: Any, truncated: list[str]) -> list[Check]:
    rows = rollup if isinstance(rollup, list) else []
    checks: list[Check] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("context") or "")
        status = str(row.get("status") or row.get("state") or "").upper()
        conclusion = str(row.get("conclusion") or "").upper()
        if not conclusion and status in {"SUCCESS", "FAILURE", "ERROR", "PENDING"}:
            # Legacy commit statuses report their result in `state`.
            conclusion = "" if status == "PENDING" else status
            status = "COMPLETED" if conclusion else "IN_PROGRESS"
        checks.append(Check(
            name=name, status=status or "COMPLETED", conclusion=conclusion,
            workflow=str(row.get("workflowName") or ""),
            url=str(row.get("detailsUrl") or row.get("targetUrl") or ""),
            required=bool(row.get("isRequired")),
        ))
    return _cap(checks, MAX_CHECKS, "checks", truncated)


def _parse_threads(value: Any, truncated: list[str]) -> list[ReviewComment]:
    comments: list[ReviewComment] = []
    try:
        nodes = value["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (TypeError, KeyError):
        return comments
    for thread in nodes or []:
        if not isinstance(thread, dict):
            continue
        thread_id = str(thread.get("id") or "")
        resolved = bool(thread.get("isResolved"))
        resolved_by = _login(thread.get("resolvedBy"))
        outdated = bool(thread.get("isOutdated"))
        for item in (thread.get("comments", {}) or {}).get("nodes", []) or []:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("databaseId") or item.get("id") or "")
            comments.append(ReviewComment(
                id=identifier, thread_id=thread_id, author=_login(item.get("author")),
                body=_clip(item.get("body")),
                path=str(item.get("path") or thread.get("path") or ""),
                line=item.get("line") or item.get("originalLine") or thread.get("line"),
                resolved=resolved, resolved_by=resolved_by,
                outdated=outdated or bool(item.get("outdated")),
                created_at=str(item.get("createdAt") or ""),
                updated_at=str(item.get("updatedAt") or ""),
                is_bot=_is_bot(item.get("author")), url=str(item.get("url") or ""),
            ))
    return _cap(comments, MAX_REVIEW_COMMENTS, "review_comments", truncated)


def collect(gh: Gh, owner: str, repo: str, number: int, *,
            now: float | None = None) -> Snapshot:
    """One bounded pass over everything the AI may need to interpret the PR."""
    truncated: list[str] = []
    partial_reasons: list[str] = []

    try:
        view = gh.json("pr", "view", str(number), "--json", PR_VIEW_FIELDS)
    except GhError as error:
        raise SnapshotError(f"could not read pull request #{number}: {error}",
                            retryable=error.reason in {"GH_RATE_LIMITED", "GH_NETWORK",
                                                       "GH_TIMEOUT"}) from error
    if not isinstance(view, dict) or "number" not in view:
        raise SnapshotError(f"gh returned no pull request #{number}")

    comments = [
        Comment(id=str(item.get("id") or ""), author=_login(item.get("author")),
                body=_clip(item.get("body")), created_at=str(item.get("createdAt") or ""),
                updated_at=str(item.get("updatedAt") or item.get("createdAt") or ""),
                is_bot=_is_bot(item.get("author")), url=str(item.get("url") or ""))
        for item in (view.get("comments") or []) if isinstance(item, dict)
    ]
    reviews = [
        Review(id=str(item.get("id") or ""), author=_login(item.get("author")),
               state=str(item.get("state") or "").upper(), body=_clip(item.get("body")),
               submitted_at=str(item.get("submittedAt") or ""),
               is_bot=_is_bot(item.get("author")))
        for item in (view.get("reviews") or []) if isinstance(item, dict)
    ]
    commits = [
        Commit(sha=str(item.get("oid") or ""), message=_clip(item.get("messageHeadline"), 500),
               author=_login((item.get("authors") or [{}])[0] if item.get("authors")
                             else item.get("author")),
               at=str(item.get("committedDate") or ""))
        for item in (view.get("commits") or []) if isinstance(item, dict)
    ]
    files = [str(item.get("path") or "") for item in (view.get("files") or [])
             if isinstance(item, dict)]
    labels = [str(item.get("name") or "") for item in (view.get("labels") or [])
              if isinstance(item, dict)]

    # Thread identity and resolution state only exist in GraphQL.
    review_comments: list[ReviewComment] = []
    try:
        threads = gh.json("api", "graphql", "-f", f"query={THREADS_QUERY}",
                          "-F", f"owner={owner}", "-F", f"repo={repo}",
                          "-F", f"number={number}")
        review_comments = _parse_threads(threads, truncated)
    except (GhError, SnapshotError) as error:
        partial_reasons.append(f"review_threads:{error}")

    snapshot = Snapshot(
        number=int(view["number"]), state=str(view.get("state") or "").upper(),
        head_sha=str(view.get("headRefOid") or ""), title=_clip(view.get("title"), 500),
        body=_clip(view.get("body")), author=_login(view.get("author")),
        head_branch=str(view.get("headRefName") or ""),
        base_branch=str(view.get("baseRefName") or ""),
        is_draft=bool(view.get("isDraft")), mergeable=str(view.get("mergeable") or ""),
        url=str(view.get("url") or ""), updated_at=str(view.get("updatedAt") or ""),
        comments=_cap(comments, MAX_COMMENTS, "comments", truncated),
        reviews=_cap(reviews, MAX_REVIEWS, "reviews", truncated),
        review_comments=review_comments,
        checks=_parse_checks(view.get("statusCheckRollup"), truncated),
        commits=_cap(commits, MAX_COMMITS, "commits", truncated),
        changed_files=_cap(files, MAX_FILES, "files", truncated),
        labels=labels,
        truncated=truncated,
        partial=bool(partial_reasons), partial_reasons=partial_reasons,
        collected_at=time.time() if now is None else now,
    )
    snapshot.linked_issues = _linked_issues(
        [snapshot.body, *[item.body for item in snapshot.comments],
         *[item.body for item in snapshot.reviews]], owner, repo)
    return snapshot
