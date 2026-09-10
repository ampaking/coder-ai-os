"""`coder-ai ship` — run the project's declared workflow, in order, with the evidence gate.

The agent calls this. It does not run git itself: the harness does, and the order
is enforced rather than trusted. Nothing mutating starts while a required check
has no evidence, and nothing continues past a failed step.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from coderai.delivery import declaration as declaration_module
from coderai.delivery.declaration import Delivery, Step
from coderai.delivery.policy import DeliveryScope, decide
from coderai.evidence import ledger as ledger_module

MAX_OUTPUT = 4000
DEFAULT_TIMEOUT = 1800

OK = "ok"
FAILED = "failed"
REFUSED = "refused"
SKIPPED = "skipped"
BLOCKED = "blocked"

# Steps that change something outside this machine, or history.
MUTATING = ("push", "commit", "rebase", "pr", "issue")
# Git history names the repository, never the tool that typed. A trailer needs its own
# line, so the single-line rule below removes that whole class by construction; these
# catch the inline forms, and EMAIL catches `Claude <noreply@anthropic.com>`.
MESSAGE_TRAILERS = ("AI-Agent:", "AI-Model:", "Co-Authored-By:", "Generated with",
                    "Claude Code", "🤖")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# One line — that is the rule. The cap is deliberately generous: delivery runs
# unattended, and refusing an otherwise-fine subject for being a few characters long
# would stall a run for a style opinion.
MAX_MESSAGE = 120


class ShipError(RuntimeError):
    """Delivery cannot start."""


@dataclass
class StepResult:
    name: str
    command: str
    status: str
    exit: int | None = None
    duration_ms: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {OK, SKIPPED}


@dataclass
class Report:
    results: list[StepResult] = field(default_factory=list)
    started: bool = False
    reason: str = ""
    dry_run: bool = False

    @property
    def failed(self) -> StepResult | None:
        return next((item for item in self.results if not item.ok), None)

    @property
    def ok(self) -> bool:
        return self.started and self.failed is None

    def render(self) -> str:
        if not self.started:
            return f"coder-ai ship: {self.reason}\n"
        head = "would run" if self.dry_run else "delivered"
        lines = [f"coder-ai ship — {head}", ""]
        for item in self.results:
            mark = {OK: "✓", SKIPPED: "·", FAILED: "✗", REFUSED: "✗", BLOCKED: "✗"}[item.status]
            timing = f"{item.duration_ms}ms" if item.duration_ms else ""
            lines.append(f"  {mark} {item.name:<10} {item.command[:52]:<52} {timing}")
            if item.detail and not item.ok:
                lines.append(f"      {item.detail[:200]}")
        failure = self.failed
        lines.append("")
        if failure is None:
            lines.append("  every declared step completed" if not self.dry_run
                         else "  nothing was run")
        else:
            lines.append(f"  stopped at '{failure.name}' — {failure.status}")
        return "\n".join(lines) + "\n"


def _run(argv: Sequence[str], cwd: Path, timeout: int) -> tuple[int, str, int]:
    started = time.monotonic()
    try:
        result = subprocess.run(list(argv), cwd=str(cwd), capture_output=True, text=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s", int((time.monotonic() - started) * 1000)
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found", 0
    output = (result.stderr or result.stdout or "").strip()[-MAX_OUTPUT:]
    return result.returncode, output, int((time.monotonic() - started) * 1000)


def current_branch(project: Path) -> str:
    try:
        result = subprocess.run(["git", "-C", str(project), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def validate_message(message: str) -> str:
    """A commit message the agent wrote, checked before it becomes history."""
    text = (message or "").strip()
    if not text:
        raise ShipError("a commit needs a message")
    if "\n" in text or "\r" in text:
        raise ShipError("commit messages are one line — no body, no trailers")
    if len(text) > MAX_MESSAGE:
        raise ShipError(f"commit message is too long ({len(text)} > {MAX_MESSAGE} chars) "
                        "— shorten the subject, or split the commit")
    for trailer in MESSAGE_TRAILERS:
        if trailer.lower() in text.lower():
            raise ShipError(f"commit messages carry no {trailer.rstrip(':')} trailer — "
                            "provider detail belongs in the audit log")
    if EMAIL.search(text):
        raise ShipError("commit messages carry no email address — the commit is "
                        "authored by the repository's identity, not by the agent")
    return text


def _gate(project: Path, task: str | None) -> tuple[bool, str]:
    """Nothing mutating runs while a required check has no evidence."""
    from coderai.evidence.prove import prove

    verdict = prove(Path(project), task)
    if verdict.verified:
        return True, ""
    failures = verdict.failures
    if not verdict.observed:
        return False, ("no evidence was recorded for this work — re-run the checks so "
                       "they can be observed, or wire the hook with `coder-ai sync`")
    first = failures[0]
    return False, (f"{len(failures)} check(s) unverified, starting with "
                   f"'{first.item}' ({first.status}) — {first.note or first.evidence}")


def _mutating(step: Step) -> bool:
    lowered = step.name.lower()
    text = " ".join(step.argv).lower()
    return any(word in lowered or word in text for word in MUTATING)


def plan_steps(delivery: Delivery, only: Sequence[str] | None) -> list[Step]:
    """`--steps` may narrow the run, but never skip a check that precedes a push."""
    if not only:
        return list(delivery.steps)
    wanted = set(only)
    unknown = wanted - {step.name for step in delivery.steps}
    if unknown:
        raise ShipError(f"unknown step(s): {', '.join(sorted(unknown))}")
    chosen: list[Step] = []
    for step in delivery.steps:
        if step.name in wanted:
            chosen.append(step)
            continue
        # A verification that comes before a selected mutating step is not optional.
        later = [item for item in delivery.steps
                 if delivery.steps.index(item) > delivery.steps.index(step)]
        if any(item.name in wanted and _mutating(item) for item in later) and not _mutating(step):
            raise ShipError(
                f"'{step.name}' runs before the step(s) you selected and verifies the work; "
                "delivery will not skip it")
    return chosen


def ship(project: Path, *, branch: str = "", message: str = "", title: str = "",
         only: Sequence[str] | None = None, dry_run: bool = False,
         task: str | None = None, allow_unverified: bool = False,
         timeout: int = DEFAULT_TIMEOUT, runner: Callable | None = None,
         is_agent: bool | None = None) -> Report:
    project = Path(project)
    execute = runner or _run
    report = Report(dry_run=dry_run)

    delivery = declaration_module.load(project)
    if delivery is None:
        # `coder-ai sync` only proposes on a terminal, so an agent or CI run told to
        # sync would loop forever. `ship enable` is the command that always asks.
        report.reason = ("this project declares no delivery workflow — "
                         "run `coder-ai ship enable` to propose one")
        return report
    if not declaration_module.is_enabled(project):
        report.reason = ("delivery is not enabled on this machine — "
                         "run `coder-ai ship enable` and confirm it")
        return report

    branch = branch or current_branch(project)
    if not delivery.matches_branch(branch) and not any(
            step.name == "branch" for step in delivery.steps):
        report.reason = (f"branch {branch!r} does not match this project's pattern "
                         f"{delivery.branch_pattern!r} — create a matching branch first")
        return report

    if allow_unverified:
        agent = is_agent if is_agent is not None else (not os.isatty(0)
                                                       or bool(os.environ.get("CODER_AI_PR_MODE")))
        if agent:
            report.reason = ("--allow-unverified is for a human decision; an agent may not "
                             "bypass the evidence gate")
            return report

    values = {"branch": branch, "base": delivery.base, "remote": delivery.remote,
              "title": title or message or branch}
    scope = DeliveryScope(project=str(project), remote=delivery.remote, base=delivery.base,
                          branch_pattern=delivery.branch_pattern)

    try:
        steps = plan_steps(delivery, only)
    except ShipError as error:
        report.reason = str(error)
        return report

    report.started = True
    gated = False
    for step in steps:
        argv = step.render(values)
        command = " ".join(argv)

        if _mutating(step) and not gated and not dry_run and not allow_unverified:
            gated = True
            passed, why = _gate(project, task)
            if not passed:
                report.results.append(StepResult(
                    name=step.name, command=command, status=BLOCKED,
                    detail=f"refusing to deliver unverified work: {why}"))
                return report

        decision = decide(argv, os.environ, project, scope)
        if not decision.allowed:
            report.results.append(StepResult(name=step.name, command=command, status=REFUSED,
                                             detail=f"{decision.code}: {decision.reason}"))
            return report

        if dry_run:
            report.results.append(StepResult(name=step.name, command=command, status=SKIPPED))
            continue

        code, output, duration = execute(argv, project, timeout)
        status = OK if code == 0 else FAILED
        report.results.append(StepResult(name=step.name, command=command, status=status,
                                         exit=code, duration_ms=duration,
                                         detail="" if code == 0 else output))
        try:
            ledger_module.append(project, ledger_module.COMMAND, command=command,
                                 exit=code, ok=code == 0)
        except Exception:
            pass
        if code != 0 and not step.optional:
            return report
    return report
