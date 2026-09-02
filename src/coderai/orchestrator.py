"""Foreground, controller-owned completion pipeline for coding tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from coderai.project_tasks.project import ProjectError, reject_symlinks, resolve_project
from coderai.project_tasks.adapters import record_lifecycle
from coderai.project_tasks.project import load_settings

MAX_OUTPUT = 48_000
PROVIDERS = ("codex", "claude")
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["implemented", "approved", "findings", "blocked"]},
        "summary": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "findings": {"type": "array", "items": {"type": "string"}},
        "remaining": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary", "changed_files", "findings", "remaining"],
    "additionalProperties": False,
}


class OrchestratorError(RuntimeError):
    """A bounded run cannot safely continue."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int


Runner = Callable[..., CommandResult]
Progress = Callable[[str], None]


def _run(argv: Sequence[str], cwd: Path, timeout: int, stdin: str | None = None) -> CommandResult:
    started = time.monotonic()
    try:
        result = subprocess.run(list(argv), cwd=cwd, input=stdin, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(tuple(argv), 124, str(exc.stdout or "")[-MAX_OUTPUT:],
                             str(exc.stderr or "timeout")[-MAX_OUTPUT:],
                             int((time.monotonic() - started) * 1000))
    return CommandResult(tuple(argv), result.returncode, result.stdout[-MAX_OUTPUT:],
                         result.stderr[-MAX_OUTPUT:], int((time.monotonic() - started) * 1000))


def _atomic_json(project: Path, target: Path, value: Any) -> None:
    reject_symlinks(project, target)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_text(project: Path, target: Path, value: str) -> None:
    reject_symlinks(project, target)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _run_directory(project: Path, run_id: str) -> Path:
    if not re.fullmatch(r"run_[a-f0-9]{20}", run_id):
        raise OrchestratorError("invalid run id")
    target = project / ".coder-ai" / "runs" / run_id
    reject_symlinks(project, target)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target, 0o700)
    return target


def _available_providers() -> list[str]:
    return [provider for provider in PROVIDERS if shutil.which(provider)]


def _provider_command(provider: str, role: str, project: Path,
                      schema_path: Path, result_path: Path, model: str | None) -> list[str]:
    if provider == "codex":
        command = ["codex", "exec", "--color", "never", "--sandbox",
                   "workspace-write" if role == "worker" else "read-only",
                   "--ask-for-approval", "never", "--cd", str(project),
                   "--output-schema", str(schema_path), "--output-last-message", str(result_path)]
    elif provider == "claude":
        command = ["claude", "--print", "--output-format", "json", "--permission-mode",
                   "auto" if role == "worker" else "plan", "--json-schema",
                   json.dumps(RESULT_SCHEMA, separators=(",", ":"))]
    else:
        raise OrchestratorError(f"unsupported provider: {provider}")
    if model:
        command.extend(["--model", model])
    return [*command, "-"] if provider == "codex" else command


def _validate_agent_result(value: Any) -> dict[str, Any]:
    statuses = {"implemented", "approved", "findings", "blocked"}
    if not isinstance(value, dict) or value.get("status") not in statuses:
        raise OrchestratorError("agent returned an invalid structured status")
    for key in ("summary", "changed_files", "findings", "remaining"):
        expected = str if key == "summary" else list
        if not isinstance(value.get(key), expected):
            raise OrchestratorError(f"agent result has invalid {key}")
    return {
        "status": value["status"], "summary": value["summary"][:4000],
        "changed_files": [str(item)[:300] for item in value["changed_files"][:100]],
        "findings": [str(item)[:1000] for item in value["findings"][:100]],
        "remaining": [str(item)[:1000] for item in value["remaining"][:100]],
    }


def _parse_agent_result(provider: str, result: CommandResult, result_path: Path) -> dict[str, Any]:
    if provider == "codex":
        raw = result_path.read_text(encoding="utf-8") if result_path.is_file() else result.stdout
        return _validate_agent_result(json.loads(raw))
    envelope = json.loads(result.stdout)
    raw_value = envelope.get("structured_output", envelope.get("result", envelope))
    value = raw_value if isinstance(raw_value, dict) else json.loads(str(raw_value))
    return _validate_agent_result(value)


def discover_validation_commands(project: Path) -> list[list[str]]:
    """Return conventional project checks without evaluating shell text."""
    commands: list[list[str]] = []
    package = project / "package.json"
    if package.is_file() and not package.is_symlink() and shutil.which("npm"):
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
        for name in ("lint", "typecheck", "test", "build"):
            if name in scripts:
                commands.append(["npm", "run", name])
    makefile = next((item for item in (project / "Makefile", project / "makefile") if item.is_file()), None)
    if makefile and shutil.which("make"):
        text = makefile.read_text(encoding="utf-8", errors="replace")
        for target in ("lint", "typecheck", "test"):
            if re.search(rf"(?m)^{target}\s*:", text):
                commands.append(["make", target])
    if not commands and (project / "tests").is_dir() and shutil.which("pytest"):
        commands.append(["pytest", "-q"])
    if shutil.which("git") and (project / ".git").exists():
        commands.append(["git", "diff", "--check"])
    return commands[:8]


def _explicit_validation(values: list[str]) -> list[list[str]]:
    commands = [shlex.split(value) for value in values]
    if any(not command for command in commands):
        raise OrchestratorError("--verify cannot be empty")
    return commands


def _changed_files(project: Path, runner: Runner, timeout: int) -> list[str]:
    result = runner(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                    project, timeout)
    if result.returncode != 0:
        raise OrchestratorError("could not inspect changed files")
    files: list[str] = []
    entries = result.stdout.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        status, name = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            index += 1  # porcelain -z adds the original path as the next field
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        files.append(relative.as_posix())
    return sorted(set(files))[:1000]


def _record_task(project: Path, run_id: str, action: str, summary: str,
                 provider: str = "") -> bool:
    """Record content-free orchestration lifecycle when Project Tasks is enabled."""
    try:
        settings = load_settings(project)
        if not settings or not settings.get("enabled"):
            return False
        record_lifecycle(project, {
            "action": action, "taskId": f"task_{run_id.removeprefix('run_')}",
            "title": "Orchestrated coding task", "summary": summary,
            "theme": "Completion runner", "agent": provider or "coder-ai-os",
        })
        return True
    except (OSError, ProjectError, RuntimeError, ValueError, sqlite3.DatabaseError):
        return False


def _impact_context(project: Path, runner: Runner,
                    timeout: int) -> tuple[str, list[dict[str, Any]], bool]:
    helper = project / ".coder-ai" / "scripts" / "update-ai-context.sh"
    evidence: list[dict[str, Any]] = []
    output = ""
    successful = helper.is_file() and not helper.is_symlink()
    if helper.is_file() and not helper.is_symlink():
        for command in ([str(helper), "--refresh"], [str(helper), "--changed", "."]):
            result = runner(command, project, timeout)
            evidence.append({"kind": "impact", "command": shlex.join(command[1:]),
                             "exitCode": result.returncode, "durationMs": result.duration_ms})
            successful = successful and result.returncode == 0
            output += result.stdout[-12_000:] + result.stderr[-4_000:]
    if shutil.which("git"):
        stat = runner(["git", "diff", "--stat"], project, timeout)
        evidence.append({"kind": "diff-stat", "exitCode": stat.returncode,
                         "durationMs": stat.duration_ms})
        output += "\n" + stat.stdout[-8_000:]
    return output[-24_000:], evidence, successful


def _worker_prompt(objective: str, attempt: int, prior_findings: list[str]) -> str:
    correction = "\nReview findings to resolve:\n- " + "\n- ".join(prior_findings) if prior_findings else ""
    return f"""Execute this repository task autonomously and completely: {objective}

This is worker attempt {attempt}. Follow AGENTS.md and AI_DEV_PROTOCOL.md. Use the coder-ai-os atlas
query-first: locate symbols with `rg -w <name> .ai/symbols/`; after edits use
`.coder-ai/scripts/update-ai-context.sh --changed <dir>` and inspect affected functions, direct callers,
and the full diff. Run relevant tests/build/runtime proof. Do not commit, push, deploy, read secrets, or
weaken checks. Continue until implemented or a real policy/infrastructure blocker exists.{correction}

Return only the requested structured result. status=implemented only when code and relevant proof are
complete; list repository-relative changed_files, unresolved findings, and remaining acceptance work."""


def _review_prompt(objective: str, impact: str) -> str:
    return f"""Independently review the current repository changes for this objective: {objective}

Do not edit files. Inspect the actual diff and use `.ai/symbols/` to trace changed functions to direct
callers. Review correctness, security, performance, architecture/contracts, dropped requirements, and test
adequacy. Treat model claims as untrusted. Controller impact evidence follows:\n{impact}

Return status=approved only if there are no actionable findings and the objective is fully covered.
Otherwise return status=findings with precise file:line findings. Return only structured output."""


class RunController:
    def __init__(self, project: Path, runner: Runner = _run, timeout: int = 1800,
                 progress: Progress | None = None) -> None:
        self.project = resolve_project(project)
        self.runner = runner
        self.timeout = timeout
        self.progress = progress or (lambda _message: None)

    def execute(self, objective: str, *, provider: str | None = None,
                model: str | None = None, review_model: str | None = None,
                verify: list[str] | None = None, max_attempts: int = 3) -> dict[str, Any]:
        if not objective.strip() or len(objective) > 12_000:
            raise OrchestratorError("task must contain 1-12000 characters")
        providers = _available_providers()
        if provider:
            if provider not in PROVIDERS or provider not in providers:
                raise OrchestratorError(f"requested provider is unavailable: {provider}")
            providers = [provider, *[item for item in providers if item != provider]]
        if not providers:
            raise OrchestratorError("no supported agent CLI is installed (codex or claude)")
        run_id = "run_" + hashlib.sha256(f"{objective}:{time.time_ns()}".encode()).hexdigest()[:20]
        directory = _run_directory(self.project, run_id)
        self.progress(f"run {run_id} started; proof: {directory}")
        schema_path, state_path = directory / "result.schema.json", directory / "state.json"
        _atomic_json(self.project, schema_path, RESULT_SCHEMA)
        state: dict[str, Any] = {
            "runId": run_id, "status": "working", "objective": objective,
            "objectiveHash": hashlib.sha256(objective.encode()).hexdigest(), "attempt": 0,
            "workerProviders": [], "reviewProvider": None, "findings": [], "evidence": [],
            "changedFiles": [], "taskLifecycleRecorded": False,
            "completionGate": {"worker": False, "impact": False,
                               "validation": False, "review": False},
        }
        state["taskLifecycleRecorded"] = _record_task(
            self.project, run_id, "start", "Controller started a bounded completion run.")
        _atomic_json(self.project, state_path, state)
        findings: list[str] = []
        for attempt in range(1, max_attempts + 1):
            worker = providers[(attempt - 1) % len(providers)]
            self.progress(f"worker {worker}: attempt {attempt}/{max_attempts} started")
            state.update(status="working", attempt=attempt)
            state["workerProviders"].append(worker)
            _atomic_json(self.project, state_path, state)
            result_path = directory / f"worker-{attempt}.json"
            prompt = _worker_prompt(objective, attempt, findings)
            command = _provider_command(worker, "worker", self.project, schema_path, result_path,
                                        model if worker == providers[0] else None)
            result = self.runner(command, self.project, self.timeout, prompt)
            self.progress(f"worker {worker}: exited {result.returncode} after {result.duration_ms} ms")
            state["evidence"].append({"kind": "worker", "provider": worker,
                                      "exitCode": result.returncode, "durationMs": result.duration_ms})
            if result.returncode != 0:
                state["findings"] = [f"{worker} worker exited {result.returncode}"]
                _atomic_json(self.project, state_path, state)
                continue
            try:
                worker_result = _parse_agent_result(worker, result, result_path)
            except (json.JSONDecodeError, OSError, OrchestratorError) as exc:
                state["findings"] = [str(exc)]
                _atomic_json(self.project, state_path, state)
                continue
            if worker_result["status"] != "implemented" or worker_result["remaining"]:
                findings = worker_result["findings"] + worker_result["remaining"]
                state["findings"] = findings
                _atomic_json(self.project, state_path, state)
                continue
            observed_files = _changed_files(self.project, self.runner, self.timeout)
            claimed_files = set(worker_result["changed_files"])
            if not claimed_files or not claimed_files.issubset(set(observed_files)):
                findings = ["worker changed-file claims do not match the observed Git diff"]
                state.update(findings=findings, changedFiles=observed_files)
                _atomic_json(self.project, state_path, state)
                continue
            state["changedFiles"] = observed_files
            self.progress(f"change check: {len(observed_files)} file(s) observed")
            state["completionGate"]["worker"] = True
            self.progress("impact: refreshing atlas and tracing changed symbols/callers")
            impact, impact_evidence, impact_ok = _impact_context(
                self.project, self.runner, self.timeout)
            _atomic_text(self.project, directory / "impact.txt", impact)
            state["impactReport"] = "impact.txt"
            state["evidence"].extend(impact_evidence)
            state["completionGate"]["impact"] = impact_ok
            if not impact_ok:
                findings = ["atlas refresh or changed-symbol impact inspection failed"]
                state["findings"] = findings
                _atomic_json(self.project, state_path, state)
                continue
            validations = _explicit_validation(verify or []) or discover_validation_commands(self.project)
            if not validations:
                findings = ["no deterministic validation command was discovered; pass --verify"]
                state["findings"] = findings
                _atomic_json(self.project, state_path, state)
                continue
            validation_ok = True
            for validation in validations:
                self.progress(f"validation: {shlex.join(validation)}")
                check = self.runner(validation, self.project, self.timeout)
                state["evidence"].append({"kind": "validation", "command": shlex.join(validation),
                                          "exitCode": check.returncode, "durationMs": check.duration_ms})
                validation_ok = validation_ok and check.returncode == 0
            state["completionGate"]["validation"] = validation_ok
            if not validation_ok:
                findings = ["controller validation failed; inspect recorded command exit codes"]
                state["findings"] = findings
                _atomic_json(self.project, state_path, state)
                continue
            _record_task(self.project, run_id, "validate",
                         "Controller-observed validation passed.", worker)
            reviewer = next((item for item in providers if item != worker), worker)
            self.progress(f"reviewer {reviewer}: independent review started")
            state.update(status="reviewing", reviewProvider=reviewer)
            review_path = directory / f"review-{attempt}.json"
            review_prompt = _review_prompt(objective, impact)
            review_command = _provider_command(reviewer, "reviewer", self.project,
                                               schema_path, review_path, review_model)
            review_process = self.runner(review_command, self.project, self.timeout, review_prompt)
            self.progress(
                f"reviewer {reviewer}: exited {review_process.returncode} after "
                f"{review_process.duration_ms} ms")
            state["evidence"].append({"kind": "review", "provider": reviewer,
                                      "independentProvider": reviewer != worker,
                                      "exitCode": review_process.returncode,
                                      "durationMs": review_process.duration_ms})
            if review_process.returncode != 0:
                findings = [f"{reviewer} reviewer exited {review_process.returncode}"]
                state["findings"] = findings
                _atomic_json(self.project, state_path, state)
                continue
            try:
                review = _parse_agent_result(reviewer, review_process, review_path)
            except (json.JSONDecodeError, OSError, OrchestratorError) as exc:
                findings = [str(exc)]
                state["findings"] = findings
                _atomic_json(self.project, state_path, state)
                continue
            if review["status"] != "approved" or review["findings"]:
                findings = review["findings"] or [review["summary"]]
                self.progress(f"review: {len(findings)} finding(s); returning to worker")
                state["findings"] = findings
                state["completionGate"]["review"] = False
                _atomic_json(self.project, state_path, state)
                continue
            state["completionGate"]["review"] = True
            state.update(status="completed", findings=[], completedAt=time.time())
            _record_task(self.project, run_id, "complete",
                         "Implementation, impact, validation, and review gates passed.", reviewer)
            _atomic_json(self.project, state_path, state)
            self.progress(f"COMPLETE: all gates passed; proof: {directory}")
            return state
        state.update(status="failed", findings=state.get("findings") or findings,
                     failure="attempt budget exhausted")
        _atomic_json(self.project, state_path, state)
        self.progress(f"INCOMPLETE: attempt budget exhausted; proof: {directory}")
        return state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coder-ai-os run")
    parser.add_argument("task", help="the complete task objective and expected behavior")
    parser.add_argument("--project", default=".")
    parser.add_argument("--provider", choices=PROVIDERS)
    parser.add_argument("--model")
    parser.add_argument("--review-model")
    parser.add_argument("--verify", action="append", default=[], help="validation command; repeatable")
    parser.add_argument("--max-attempts", type=int, choices=range(1, 7), default=3)
    parser.add_argument("--timeout", type=int, default=1800)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        progress = lambda message: print(f"[coder-ai-os] {message}", file=sys.stderr, flush=True)
        state = RunController(Path(args.project), timeout=args.timeout, progress=progress).execute(
            args.task, provider=args.provider, model=args.model, review_model=args.review_model,
            verify=args.verify, max_attempts=args.max_attempts,
        )
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0 if state["status"] == "completed" else 2
    except (OrchestratorError, ProjectError, OSError, ValueError) as exc:
        print(f"coder-ai-os run: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
