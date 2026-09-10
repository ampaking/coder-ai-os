"""Build throwaway git repositories for PR automation tests.

A bare 'origin' plus a working clone, with a PR head branch — enough to exercise
fetch, worktree add, commit, and push against a real git, with no network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                            text=True, check=True)
    return result.stdout.strip()


def _identity(path: Path) -> None:
    git(path, "config", "user.email", "coder-ai-test@example.invalid")
    git(path, "config", "user.name", "coder-ai-os Test")
    git(path, "config", "commit.gpgsign", "false")


def build(root: Path, *, head_branch: str = "feature/worker-retry",
          base_branch: str = "main") -> tuple[Path, Path]:
    """Return (origin_bare_path, clone_path) with head_branch present on both."""
    origin = root / "origin.git"
    seed = root / "seed"
    clone = root / "work"

    subprocess.run(["git", "init", "-q", "--bare", "-b", base_branch, str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", base_branch, str(seed)], check=True)
    _identity(seed)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    (seed / "worker").mkdir()
    (seed / "worker" / "retry.py").write_text("def retry():\n    return 1\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "chore: seed")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", base_branch)

    git(seed, "checkout", "-qb", head_branch)
    (seed / "worker" / "retry.py").write_text(
        "def retry():\n    # PR change\n    return 2\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "fix(worker): retry")
    git(seed, "push", "-q", "origin", head_branch)

    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _identity(clone)
    return origin, clone


def add_remote_commit(origin: Path, root: Path, branch: str, message: str = "human push") -> str:
    """Simulate a human pushing to the PR branch while coder-ai-os is running."""
    scratch = root / f"human-{abs(hash(message)) % 10000}"
    subprocess.run(["git", "clone", "-q", "-b", branch, str(origin), str(scratch)], check=True)
    _identity(scratch)
    (scratch / "HUMAN.md").write_text(message + "\n", encoding="utf-8")
    git(scratch, "add", "-A")
    git(scratch, "commit", "-qm", message)
    git(scratch, "push", "-q", "origin", branch)
    return git(scratch, "rev-parse", "HEAD")
