"""Isolated automation workspace for one Pull Request (§12).

    ~/.coder-ai/pr-worktrees/<owner>/<repo>/pr-<n>/

PR automation never operates in the engineer's checkout. They stay free to switch
branches, run Docker, and run their own agents while coder-ai-os works.

The worktree checks out a coder-ai-os-owned local branch, `coder-ai/pr-<n>`, whose upstream
is the PR head branch. That keeps the engineer's own local branch untouched even
when they have it checked out, while a plain `git push` from the agent still goes
to exactly one place: the PR head ref (push.default=upstream).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from coderai.pr_automation import audit
from coderai.pr_automation.gitcmd import Git, GitError
from coderai.pr_automation.state import (
    Session, StateError, _safe, reject_symlinks, worktrees_root,
)

CREATED = "CREATED"
REUSED = "REUSED"
RECREATED = "RECREATED"
REMOVED = "REMOVED"
KEPT_DIRTY = "KEPT_DIRTY"


class WorktreeError(RuntimeError):
    """The isolated workspace cannot be created or trusted."""


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    sha: str
    status: str
    push_supported: bool
    upstream: str


def local_branch(number: int) -> str:
    return f"coder-ai/pr-{int(number)}"


def worktree_path(owner: str, repo: str, number: int, *, root: Path | None = None,
                  create: bool = True) -> Path:
    """Where this PR's workspace lives. `create=False` computes it without touching disk."""
    base = (root or worktrees_root()).expanduser()
    target = (base / _safe(owner, "owner") / _safe(repo, "repository")
              / _safe(f"pr-{int(number)}", "pull request"))
    if create:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if base.exists():
        reject_symlinks(base, target)
    return target


def _fetch_head(git: Git, session: Session, cross_repository: bool) -> str:
    """Fetch the PR head into the source repository and return its sha."""
    refspec = (f"pull/{session.number}/head" if cross_repository else session.head_branch)
    try:
        git.check("fetch", "--no-tags", "--quiet", session.remote, refspec, timeout=180)
    except GitError as error:
        raise WorktreeError(f"could not fetch the PR head ({refspec}): {error}") from error
    sha = git.maybe("rev-parse", "FETCH_HEAD")
    if not sha:
        raise WorktreeError("git did not report a sha for the fetched PR head")
    return sha


def _registered(git: Git, path: Path) -> bool:
    listing = git.maybe("worktree", "list", "--porcelain") or ""
    target = str(path.resolve()) if path.exists() else str(path)
    for line in listing.splitlines():
        if line.startswith("worktree "):
            recorded = line[len("worktree "):].strip()
            if recorded == target or Path(recorded).resolve(strict=False) == Path(target):
                return True
    return False


def _prune(git: Git, path: Path) -> None:
    """Drop a stale registration and directory left by a killed run.

    `worktree remove` fails when the tree's .git link is broken, so the directory
    is removed first and pruned afterwards — otherwise git keeps the branch bound
    to a worktree that no longer exists and refuses to recreate it.
    """
    git.run("worktree", "remove", "--force", str(path))
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    git.run("worktree", "prune")


def _release_elsewhere(previous: str, path: Path, session_directory: Path | None) -> None:
    """Drop the worktree registration held by a different checkout.

    A worktree belongs to exactly one repository's git directory. When the same PR
    is driven from a second checkout, reusing the directory would leave coder-ai-os
    operating a tree owned by another clone's git dir. coder-ai-os created that
    registration, so coder-ai-os removes it — and touches nothing else in that checkout.
    """
    source = Path(previous)
    if not source.is_dir():
        return
    old = Git(source)
    if not old.is_repo():
        return
    old.run("worktree", "remove", "--force", str(path))
    old.run("worktree", "prune")
    if session_directory is not None:
        audit.append(session_directory, "worktree_source_changed",
                     previous=str(source), worktree=str(path))


def ensure_worktree(session: Session, source: Path, *, git: Git | None = None,
                    root: Path | None = None, cross_repository: bool = False,
                    session_directory: Path | None = None) -> Worktree:
    """Create or reuse the PR's isolated workspace, verified before it is used."""
    source_git = git or Git(source)
    if not Path(source).is_dir() or not source_git.is_repo():
        raise WorktreeError(f"not a git repository: {source}")

    resolved_source = Path(source).resolve()
    path = worktree_path(session.owner, session.repo, session.number, root=root)
    branch = local_branch(session.number)
    upstream = f"{session.remote}/{session.head_branch}"

    switching = (bool(session.source_repo)
                 and Path(session.source_repo) != resolved_source)
    if switching:
        _release_elsewhere(session.source_repo, path, session_directory)

    sha = _fetch_head(source_git, session, cross_repository)

    status = REUSED
    if path.exists() or _registered(source_git, path):
        tree = source_git.at(path)
        healthy = (
            (path / ".git").exists()
            and tree.is_repo()
            and tree.current_branch() == branch
            # Owned by THIS checkout: a tree registered elsewhere is not ours to use.
            and _registered(source_git, path)
        )
        if not healthy:
            _prune(source_git, path)
            status = RECREATED
    else:
        status = CREATED

    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            source_git.check("worktree", "add", "--quiet", "-B", branch, str(path), sha,
                             timeout=300)
        except GitError as error:
            raise WorktreeError(f"could not create the PR worktree: {error}") from error

    tree = source_git.at(path)
    # A plain `git push` from the agent must resolve to the PR head ref and nothing else.
    tree.run("config", "push.default", "upstream")
    tree.run("config", "advice.detachedHead", "false")
    if not cross_repository:
        tree.run("branch", f"--set-upstream-to={upstream}", branch)

    actual_branch = tree.current_branch()
    if actual_branch != branch:
        raise WorktreeError(
            f"worktree is on {actual_branch!r}, expected {branch!r} — refusing to use it"
        )
    actual_sha = tree.head_sha() or ""
    session.source_repo = str(resolved_source)
    session.worktree = str(path)
    return Worktree(path=path, branch=branch, sha=actual_sha, status=status,
                    push_supported=not cross_repository, upstream=upstream)


def refresh_to_head(session: Session, source: Path, sha: str, *, git: Git | None = None,
                    root: Path | None = None) -> str:
    """Fast-forward the worktree onto a new PR head. Never discards local work."""
    source_git = git or Git(source)
    path = worktree_path(session.owner, session.repo, session.number, root=root)
    tree = source_git.at(path)
    if not tree.is_repo():
        raise WorktreeError("worktree is missing; recreate it before refreshing")
    if not tree.is_clean():
        return "DIRTY"
    current = tree.head_sha() or ""
    if current == sha:
        return "UP_TO_DATE"
    if not tree.is_ancestor(current, sha):
        return "DIVERGED"  # a human rewrote history; the AI must not paper over it
    result = tree.run("merge", "--ff-only", sha)
    return "FAST_FORWARDED" if result.ok else "DIVERGED"


def release_worktree(session: Session, source: Path, reason: str, *, git: Git | None = None,
                     root: Path | None = None, force: bool = False,
                     session_directory: Path | None = None) -> str:
    """Remove the workspace on a terminal state, never silently losing AI work."""
    source_git = git or Git(source)
    path = worktree_path(session.owner, session.repo, session.number, root=root)
    if not path.exists():
        return REMOVED
    tree = source_git.at(path)
    dirty = tree.dirty_files() if tree.is_repo() else []
    if dirty and session_directory is not None:
        audit.append(session_directory, "worktree_uncommitted", reason=reason,
                     files=dirty, worktree=str(path))
    if dirty and not force:
        return KEPT_DIRTY
    _prune(source_git, path)
    if session_directory is not None:
        audit.append(session_directory, "worktree_removed", reason=reason, worktree=str(path))
    return REMOVED
