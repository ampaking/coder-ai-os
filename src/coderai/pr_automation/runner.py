"""Dispatch a parsed `coder-ai pr` invocation to the supervisor.

Kept separate from `cli.py` so argument parsing stays pure and side-effect free
(and unit-testable without touching git, gh, or the filesystem).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from coderai.pr_automation import commands, daemon
from coderai.pr_automation.cli import PrCommand, PrRun
from coderai.pr_automation.state import ensure_dir, session_dir


def dispatch(invocation: PrRun | PrCommand) -> int:
    """Route by the invocation's own `kind`.

    Not `isinstance`: running the CLI as `python -m …cli` executes the module a
    second time under the name `__main__`, so the class the parser produced is a
    different object from the one imported here even though both are `PrRun`.
    """
    if getattr(invocation, "kind", "") == "run":
        return run(invocation)
    return command(invocation)


def run(invocation: PrRun, *, cwd: Path | None = None) -> int:
    from coderai.pr_automation.resolve import Gh, ResolveError, resolve
    from coderai.pr_automation.supervisor import SupervisorError, run_session

    cwd = Path(cwd or os.getcwd())

    if invocation.dry_run:
        from coderai.pr_automation.rehearsal import rehearse

        try:
            print(rehearse(invocation, cwd))
        except ResolveError as error:
            print(f"coder-ai pr: {error.reason}: {error.message}", file=sys.stderr)
            return 2
        return 0

    if invocation.background:
        # Resolve first, so a bad target fails in the foreground where it is visible.
        try:
            pull_request = resolve(invocation.target, cwd, Gh(cwd))
        except ResolveError as error:
            print(f"coder-ai pr: {error.reason}: {error.message}", file=sys.stderr)
            return 2
        directory = ensure_dir(session_dir(pull_request.owner, pull_request.repo,
                                           pull_request.number))
        argv = [sys.executable, "-m", "coderai.pr_automation", str(pull_request.number),
                "--watch", _watch_argument(invocation), "--", *invocation.provider_argv]
        pid = daemon.spawn(argv, directory, cwd=cwd)
        print(f"coder-ai pr: supervising #{pull_request.number} in the background (pid {pid})")
        print(f"  coder-ai pr status {pull_request.number}   ·   "
              f"coder-ai pr log {pull_request.number}   ·   "
              f"coder-ai pr stop {pull_request.number}")
        return 0

    try:
        report = run_session(invocation, cwd)
    except SupervisorError as error:
        print(f"coder-ai pr: {error}", file=sys.stderr)
        return 2
    return 0 if report.reason not in {"", "UNRESPONSIVE"} else 1


def _watch_argument(invocation: PrRun) -> str:
    if invocation.watch.mode == "until-close":
        return "until-close"
    if invocation.watch.mode == "single":
        return "0"
    return f"{int(invocation.watch.seconds or 0)}s"


def command(invocation: PrCommand) -> int:
    if invocation.name == "status":
        return commands.status(invocation.target, as_json=invocation.as_json)
    if invocation.name == "log":
        return commands.log(str(invocation.target))
    if invocation.name == "stop":
        return commands.stop(str(invocation.target))
    if invocation.name == "attach":
        return commands.attach(str(invocation.target))
    print(f"coder-ai pr: unknown command {invocation.name}", file=sys.stderr)
    return 2
