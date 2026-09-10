"""Resolve a PR number, branch name, or PR URL into one supervised Pull Request.

§8 of the design: a branch is only a locator. Once resolved, coder-ai-os binds the
session to the Pull Request — the PR is the lifecycle object, not the branch.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from coderai.pr_automation.github import Gh, GhError, GH_UNAUTHENTICATED

NOT_A_REPO = "NOT_A_REPO"
NO_PR_FOR_BRANCH = "NO_PR_FOR_BRANCH"
AMBIGUOUS_PR = "AMBIGUOUS_PR"
PR_NOT_FOUND = "PR_NOT_FOUND"
PR_ALREADY_CLOSED = "PR_ALREADY_CLOSED"
PR_ALREADY_MERGED = "PR_ALREADY_MERGED"
REPO_MISMATCH = "REPO_MISMATCH"
BAD_TARGET = "BAD_TARGET"

PR_FIELDS = (
    "number,title,body,author,state,isDraft,url,headRefName,baseRefName,"
    "headRefOid,mergeable,isCrossRepository"
)

_URL = re.compile(
    r"^https?://(?P<host>[A-Za-z0-9._-]+)/(?P<owner>[A-Za-z0-9._-]+)/"
    r"(?P<repo>[A-Za-z0-9._-]+)/pull/(?P<number>\d+)(?:[/#?].*)?$"
)
_NUMBER = re.compile(r"^#?(\d{1,9})$")
# Git forbids these in ref names; a branch target carrying them is hostile input,
# not a typo — it never reaches a worktree path (Task 04) or a gh argument.
_BAD_REF = re.compile(r"(^-)|(\.\.)|([\x00-\x20~^:?*\[\\])|(@\{)|(\.lock$)|(/$)|(^/)")


class ResolveError(RuntimeError):
    """A target that cannot be bound to exactly one supervisable Pull Request."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class PullRequest:
    host: str
    owner: str
    repo: str
    number: int
    title: str
    author: str
    state: str
    is_draft: bool
    url: str
    head_branch: str
    base_branch: str
    head_sha: str
    remote: str
    cross_repository: bool = False

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Target:
    kind: str          # "number" | "branch" | "url"
    number: int | None = None
    branch: str | None = None
    host: str | None = None
    owner: str | None = None
    repo: str | None = None


def parse_target(value: str) -> Target:
    """Classify the user's target without touching the network."""
    text = value.strip()
    if not text:
        raise ResolveError(BAD_TARGET, "empty PR target")
    match = _URL.match(text)
    if match:
        return Target(
            kind="url", number=int(match.group("number")), host=match.group("host"),
            owner=match.group("owner"), repo=match.group("repo"),
        )
    if text.lower().startswith(("http://", "https://")):
        raise ResolveError(BAD_TARGET, f"not a pull request URL: {text}")
    match = _NUMBER.match(text)
    if match:
        number = int(match.group(1))
        if number == 0:
            raise ResolveError(BAD_TARGET, "PR numbers start at 1")
        return Target(kind="number", number=number)
    if _BAD_REF.search(text) or len(text) > 255:
        raise ResolveError(BAD_TARGET, f"not a valid branch name: {text!r}")
    return Target(kind="branch", branch=text)


def _git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                                text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _remote_for(cwd: Path, owner: str, repo: str) -> str:
    """Name the remote that points at owner/repo, defaulting to origin.

    The push guard (Task 05) scopes pushes to this exact remote, so guessing
    'origin' when a differently-named remote holds the PR would be a real bug.
    """
    listing = _git(cwd, "remote") or ""
    names = [line.strip() for line in listing.splitlines() if line.strip()]
    wanted = f"{owner}/{repo}".lower()
    for name in ("origin", *[item for item in names if item != "origin"]):
        if name not in names:
            continue
        url = (_git(cwd, "remote", "get-url", name) or "").lower()
        normalized = url.removesuffix(".git").replace(":", "/")
        if normalized.endswith("/" + wanted):
            return name
    return "origin" if "origin" in names else (names[0] if names else "origin")


def _repo_context(gh: Gh) -> tuple[str, str, str]:
    """(host, owner, repo) for the repository the working directory belongs to."""
    try:
        value = gh.json("repo", "view", "--json", "owner,name,url")
    except GhError as error:
        if error.reason == GH_UNAUTHENTICATED:
            raise ResolveError(error.reason, str(error)) from error
        raise ResolveError(
            NOT_A_REPO,
            "not inside a GitHub repository (or the remote is unreachable): " + error.message,
        ) from error
    if not isinstance(value, dict) or not value.get("name"):
        raise ResolveError(NOT_A_REPO, "gh did not report a repository for this directory")
    owner = (value.get("owner") or {}).get("login", "")
    host = "github.com"
    url = str(value.get("url", ""))
    match = re.match(r"^https?://([^/]+)/", url)
    if match:
        host = match.group(1)
    return host, owner, str(value["name"])


def _view_pr(gh: Gh, number: int, repo_flag: list[str]) -> dict[str, Any]:
    try:
        value = gh.json("pr", "view", str(number), *repo_flag, "--json", PR_FIELDS)
    except GhError as error:
        if error.reason in {"GH_NOT_FOUND"}:
            raise ResolveError(PR_NOT_FOUND, f"no pull request #{number} in this repository") from error
        raise ResolveError(error.reason, str(error)) from error
    if not isinstance(value, dict) or "number" not in value:
        raise ResolveError(PR_NOT_FOUND, f"gh returned no pull request for #{number}")
    return value


def _find_by_branch(gh: Gh, branch: str, repo_flag: list[str]) -> int:
    try:
        value = gh.json("pr", "list", "--head", branch, "--state", "open", *repo_flag,
                        "--json", "number,title,headRefName,url")
    except GhError as error:
        raise ResolveError(error.reason, str(error)) from error
    rows = [row for row in (value or []) if isinstance(row, dict)]
    if not rows:
        raise ResolveError(
            NO_PR_FOR_BRANCH,
            f"no open pull request has head branch {branch!r}; "
            "open one first, or pass the PR number",
        )
    if len(rows) > 1:
        listed = ", ".join(f"#{row.get('number')} {row.get('title', '')!r}".strip() for row in rows)
        raise ResolveError(
            AMBIGUOUS_PR,
            f"branch {branch!r} has {len(rows)} open pull requests ({listed}); "
            "pass the PR number instead",
        )
    return int(rows[0]["number"])


def resolve(target: str, cwd: Path, gh: Gh | None = None,
            *, allow_terminal: bool = False) -> PullRequest:
    """Bind a target to exactly one open Pull Request, or raise a named reason."""
    cwd = Path(cwd)
    client = gh or Gh(cwd)
    client.require()
    parsed = parse_target(target)
    host, owner, repo = _repo_context(client)
    repo_flag: list[str] = []

    if parsed.kind == "url":
        if (parsed.owner or "").lower() != owner.lower() or (parsed.repo or "").lower() != repo.lower():
            raise ResolveError(
                REPO_MISMATCH,
                f"that URL points at {parsed.owner}/{parsed.repo}, but this directory is "
                f"{owner}/{repo}; run coder-ai from the matching checkout",
            )
        if parsed.host and parsed.host != host:
            raise ResolveError(
                REPO_MISMATCH,
                f"that URL is on {parsed.host}, but this repository is on {host}",
            )
        number = int(parsed.number or 0)
    elif parsed.kind == "number":
        number = int(parsed.number or 0)
    else:
        number = _find_by_branch(client, str(parsed.branch), repo_flag)

    data = _view_pr(client, number, repo_flag)
    state = str(data.get("state", "")).upper()
    if not allow_terminal:
        if state == "MERGED":
            raise ResolveError(PR_ALREADY_MERGED,
                               f"pull request #{number} is already merged; nothing to supervise")
        if state == "CLOSED":
            raise ResolveError(PR_ALREADY_CLOSED,
                               f"pull request #{number} is closed; reopen it to supervise it")

    head_branch = str(data.get("headRefName") or "")
    if not head_branch or _BAD_REF.search(head_branch):
        raise ResolveError(BAD_TARGET, f"pull request #{number} has an unusable head ref")

    author = data.get("author") or {}
    return PullRequest(
        host=host, owner=owner, repo=repo, number=int(data["number"]),
        title=str(data.get("title") or ""),
        author=str(author.get("login") or "") if isinstance(author, dict) else "",
        state=state, is_draft=bool(data.get("isDraft")), url=str(data.get("url") or ""),
        head_branch=head_branch, base_branch=str(data.get("baseRefName") or ""),
        head_sha=str(data.get("headRefOid") or ""),
        remote=_remote_for(cwd, owner, repo),
        cross_repository=bool(data.get("isCrossRepository")),
    )
