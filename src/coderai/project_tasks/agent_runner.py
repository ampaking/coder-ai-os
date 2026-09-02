"""Launch installed agents with bounded project explanation prompts."""

from __future__ import annotations

import shutil
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from coderai.project_tasks.coaching import explain_project
from coderai.project_tasks.storage import StorageError

_LAUNCH_LOCK = threading.Lock()
_LAST_LAUNCH: dict[tuple[str, str], float] = {}
_LAUNCH_COOLDOWN_SECONDS = 10.0


def agent_command(provider: str, project: Path, prompt: str) -> list[str]:
    if provider == "codex":
        return ["codex", "--sandbox", "read-only", "--cd", str(project), prompt]
    if provider == "claude":
        return ["claude", "--permission-mode", "plan", prompt]
    raise StorageError("provider must be codex or claude")


def terminal_command(project: Path, command: list[str]) -> list[str]:
    """Build a visible terminal launcher without interpolating unquoted input."""
    shell_command = f"cd {shlex.quote(str(project))} && exec {shlex.join(command)}"
    if sys.platform == "darwin":
        script = 'on run argv\ntell application "Terminal" to activate\ntell application "Terminal" to do script item 1 of argv\nend run'
        return ["osascript", "-e", script, shell_command]
    terminal = next((name for name in ("x-terminal-emulator", "gnome-terminal", "konsole")
                     if shutil.which(name)), None)
    if terminal == "gnome-terminal":
        return [terminal, "--", "sh", "-lc", shell_command]
    if terminal == "konsole":
        return [terminal, "-e", "sh", "-lc", shell_command]
    if terminal:
        return [terminal, "-e", "sh", "-lc", shell_command]
    raise StorageError("no supported terminal launcher is installed")


def agent_capabilities(project: Path) -> dict[str, dict[str, Any]]:
    """Describe launch availability without exposing project data."""
    try:
        terminal_command(project, ["true"])
        terminal_available = True
    except StorageError:
        terminal_available = False
    return {
        provider: {
            "available": bool(shutil.which(provider)) and terminal_available,
            "providerInstalled": bool(shutil.which(provider)),
            "terminalSupported": terminal_available,
            "mode": "read-only" if provider == "codex" else "plan",
        }
        for provider in ("codex", "claude")
    }


def _agent_prompt(project: Path, question: object, period: str = "week",
                  anchor: str | None = None) -> tuple[str, dict[str, Any]]:
    context = explain_project(project, question, period, anchor)
    prompt = (
        "You are a private project guide. Explain, do not edit files. Separate facts from inference. "
        "Do not score productivity, people, engineering value, health, or job risk. Missing data is unknown. "
        f"Question: {context['question']}\nStructured evidence:\n- " + "\n- ".join(context["evidence"]) +
        f"\nCaution: {context['caution']}\nReturn a short explanation, uncertainty, and one safe next step."
    )
    return prompt, context


def launch_agent(project: Path, provider: str, question: object, period: str = "week",
                 anchor: str | None = None) -> dict[str, Any]:
    """Start a read-only interactive agent in a visible terminal and return immediately."""
    if shutil.which(provider) is None:
        raise StorageError(f"{provider} CLI is not installed")
    prompt, context = _agent_prompt(project, question, period, anchor)
    launcher = terminal_command(project, agent_command(provider, project, prompt))
    launch_key = (str(project.resolve()), provider)
    with _LAUNCH_LOCK:
        elapsed = time.monotonic() - _LAST_LAUNCH.get(launch_key, float("-inf"))
        if elapsed < _LAUNCH_COOLDOWN_SECONDS:
            raise StorageError(f"{provider} was just opened; wait before launching another session")
        _LAST_LAUNCH[launch_key] = time.monotonic()
    try:
        process = subprocess.Popen(
            launcher, cwd=project, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except StorageError:
        with _LAUNCH_LOCK:
            _LAST_LAUNCH.pop(launch_key, None)
        raise
    except OSError as exc:
        with _LAUNCH_LOCK:
            _LAST_LAUNCH.pop(launch_key, None)
        raise StorageError(f"could not launch {provider}: {exc}") from exc
    time.sleep(0.15)
    return_code = process.poll()
    if return_code not in {None, 0}:
        with _LAUNCH_LOCK:
            _LAST_LAUNCH.pop(launch_key, None)
        raise StorageError(f"could not open {provider} terminal")
    if return_code is None:
        threading.Thread(target=process.wait, daemon=True).start()
    return {
        "provider": provider,
        "terminalOpened": True,
        "agentInitialized": "unknown-check-terminal",
        "mode": "interactive-read-only",
        "stored": False,
        "privacy": "The prompt is not stored by Project Tasks; it is transiently visible in local process arguments.",
        "caution": context["caution"],
        "redactions": context["redactions"],
    }
