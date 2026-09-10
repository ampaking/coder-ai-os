"""Bounded, argv-only access to GitHub through the `gh` CLI.

Every call is a list of arguments — never a shell string — with a timeout and an
output cap. Failures are classified into named reasons so callers can print an
instruction instead of a stack trace.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

MAX_OUTPUT = 4_000_000  # a large PR's GraphQL payload, still bounded
DEFAULT_TIMEOUT = 60

GH_MISSING = "GH_MISSING"
GH_UNAUTHENTICATED = "GH_UNAUTHENTICATED"
GH_RATE_LIMITED = "GH_RATE_LIMITED"
GH_NETWORK = "GH_NETWORK"
GH_TIMEOUT = "GH_TIMEOUT"
GH_NOT_FOUND = "GH_NOT_FOUND"
GH_FAILED = "GH_FAILED"

_HINTS = {
    GH_MISSING: "install the GitHub CLI: https://cli.github.com  (brew install gh)",
    GH_UNAUTHENTICATED: "run: gh auth login",
    GH_RATE_LIMITED: "GitHub API rate limit reached; coder-ai-os will back off and retry",
    GH_NETWORK: "check your network connection",
    GH_TIMEOUT: "gh did not respond in time",
}


class GhError(RuntimeError):
    """A `gh` invocation failed in a way the caller should name, not guess at."""

    def __init__(self, reason: str, message: str, *, argv: Sequence[str] = ()) -> None:
        hint = _HINTS.get(reason)
        super().__init__(f"{message}{f' — {hint}' if hint else ''}")
        self.reason = reason
        self.message = message
        self.argv = tuple(argv)


@dataclass(frozen=True)
class GhResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int


Runner = Callable[[Sequence[str], Path, int], GhResult]


def _run(argv: Sequence[str], cwd: Path, timeout: int) -> GhResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv), cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise GhError(GH_TIMEOUT, f"gh timed out after {timeout}s", argv=argv) from None
    except FileNotFoundError:
        raise GhError(GH_MISSING, "the GitHub CLI ('gh') is not installed", argv=argv) from None
    return GhResult(
        tuple(argv), completed.returncode,
        completed.stdout[:MAX_OUTPUT], completed.stderr[:MAX_OUTPUT],
        int((time.monotonic() - started) * 1000),
    )


def classify(result: GhResult) -> str:
    """Map a failed gh result onto a named reason."""
    text = f"{result.stderr}\n{result.stdout}".lower()
    if "gh auth login" in text or "authentication" in text or "not logged in" in text:
        return GH_UNAUTHENTICATED
    if "rate limit" in text or "secondary rate" in text or "abuse detection" in text:
        return GH_RATE_LIMITED
    if "could not resolve host" in text or "dial tcp" in text or "network is unreachable" in text:
        return GH_NETWORK
    if "not found" in text or "no such" in text or "could not resolve to a" in text:
        return GH_NOT_FOUND
    return GH_FAILED


class Gh:
    """A `gh` caller with an injectable runner (tests pass a fixture runner)."""

    def __init__(self, cwd: Path, runner: Runner | None = None,
                 timeout: int = DEFAULT_TIMEOUT) -> None:
        self.cwd = Path(cwd)
        self.runner = runner or _run
        self.timeout = timeout

    def available(self) -> bool:
        return self.runner is not _run or shutil.which("gh") is not None

    def require(self) -> None:
        if not self.available():
            raise GhError(GH_MISSING, "the GitHub CLI ('gh') is required for coder-ai pr")

    def run(self, *args: str, timeout: int | None = None) -> GhResult:
        self.require()
        argv = ["gh", *args]
        try:
            result = self.runner(argv, self.cwd, timeout or self.timeout)
        except FileNotFoundError:
            # Classified here rather than only in _run, so every runner behaves alike.
            raise GhError(GH_MISSING, "the GitHub CLI ('gh') is not installed", argv=argv) from None
        if result.returncode != 0:
            reason = classify(result)
            detail = (result.stderr.strip() or result.stdout.strip() or
                      f"gh exited {result.returncode}")
            raise GhError(reason, detail.splitlines()[0][:400], argv=result.argv)
        return result

    def json(self, *args: str, timeout: int | None = None) -> Any:
        result = self.run(*args, timeout=timeout)
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError as exc:
            raise GhError(GH_FAILED, f"gh returned output that is not JSON: {exc}",
                          argv=result.argv) from exc
