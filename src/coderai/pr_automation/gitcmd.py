"""Argv-only git execution with a pinned working directory.

coder-ai-os's own git calls go through here. The AI's git calls go through the guard
(Task 05) instead — this module is the supervisor's hand, not the agent's.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

MAX_OUTPUT = 200_000
DEFAULT_TIMEOUT = 120


class GitError(RuntimeError):
    def __init__(self, message: str, *, argv: Sequence[str] = (), stderr: str = "") -> None:
        super().__init__(message)
        self.argv = tuple(argv)
        self.stderr = stderr


@dataclass(frozen=True)
class GitResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        return self.stdout.strip()


Runner = Callable[[Sequence[str], Path, int], GitResult]


def run_git(argv: Sequence[str], cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> GitResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv), cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise GitError(f"git timed out after {timeout}s", argv=argv) from None
    except FileNotFoundError:
        # FileNotFoundError here means either the binary or the cwd — say which.
        if not Path(cwd).is_dir():
            raise GitError(f"no such directory: {cwd}", argv=argv) from None
        raise GitError("'git' is not installed", argv=argv) from None
    return GitResult(tuple(argv), completed.returncode, completed.stdout[:MAX_OUTPUT],
                     completed.stderr[:MAX_OUTPUT], int((time.monotonic() - started) * 1000))


class Git:
    """Run git in exactly one directory, never through a shell."""

    def __init__(self, cwd: Path, runner: Runner | None = None,
                 timeout: int = DEFAULT_TIMEOUT) -> None:
        self.cwd = Path(cwd)
        self.runner = runner or run_git
        self.timeout = timeout

    def at(self, cwd: Path) -> "Git":
        return Git(cwd, self.runner, self.timeout)

    def run(self, *args: str, timeout: int | None = None) -> GitResult:
        return self.runner(["git", "-C", str(self.cwd), *args], self.cwd,
                           timeout or self.timeout)

    def check(self, *args: str, timeout: int | None = None) -> GitResult:
        result = self.run(*args, timeout=timeout)
        if not result.ok:
            detail = (result.stderr.strip() or result.stdout.strip()
                      or f"git exited {result.returncode}")
            raise GitError(f"git {' '.join(args[:3])}: {detail.splitlines()[0][:300]}",
                           argv=result.argv, stderr=result.stderr)
        return result

    def text(self, *args: str) -> str:
        return self.check(*args).text

    def maybe(self, *args: str) -> str | None:
        result = self.run(*args)
        return result.text if result.ok else None

    def is_repo(self) -> bool:
        return self.maybe("rev-parse", "--git-dir") is not None

    def toplevel(self) -> str | None:
        return self.maybe("rev-parse", "--show-toplevel")

    def head_sha(self) -> str | None:
        return self.maybe("rev-parse", "HEAD")

    def current_branch(self) -> str | None:
        value = self.maybe("rev-parse", "--abbrev-ref", "HEAD")
        return None if value in (None, "HEAD") else value

    def is_clean(self) -> bool:
        return self.maybe("status", "--porcelain") == ""

    def dirty_files(self) -> list[str]:
        """Paths with uncommitted changes.

        Parses raw stdout, not `.text`: stripping would eat the leading space of
        an unstaged status column (" M path") and shift every path by one byte.
        """
        result = self.run("status", "--porcelain")
        if not result.ok:
            return []
        files: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]
            if " -> " in path:  # rename: report the destination
                path = path.split(" -> ", 1)[1]
            files.append(path.strip('"'))
        return files[:200]

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self.run("merge-base", "--is-ancestor", older, newer).ok
