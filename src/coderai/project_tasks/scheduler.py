"""Explicit macOS launchd lifecycle for a short-lived local notifier."""

from __future__ import annotations

import hashlib
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from coderai.project_tasks.project import ProjectError


def _label(project: Path) -> str:
    suffix = hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:16]
    return f"local.coder-ai-os.project-tasks.{suffix}"


def _path(project: Path) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_label(project)}.plist"


def scheduler_status(project: Path) -> dict[str, Any]:
    path = _path(project)
    configured = path.is_file() and not path.is_symlink()
    loaded = False
    if configured and platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["launchctl", "print", f"gui/{os.getuid()}/{_label(project)}"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            loaded = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            loaded = False
    return {"installed": configured and loaded, "configured": configured, "platform": platform.system(),
            "intervalMode": "daily-at-18:00-local", "label": _label(project)}


def install_scheduler(project: Path, interval: int = 1800) -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise ProjectError("native notification scheduling currently supports macOS only")
    if not 300 <= interval <= 86_400:
        raise ProjectError("notification interval must be between 300 and 86400 seconds")
    executable = str(Path(sys.executable).resolve())
    cli = str((Path(__file__).parent / "cli.py").resolve())
    path = _path(project)
    if path.is_symlink() or path.parent.is_symlink():
        raise ProjectError("refusing unsafe LaunchAgents path")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{_label(project)}"
    subprocess.run(["launchctl", "bootout", service], capture_output=True, text=True,
                   check=False, timeout=15)
    payload = {
        "Label": _label(project), "ProgramArguments": [executable, cli, "--project", str(project.resolve()),
                                                         "notify", "run", "--deliver"],
        "StartCalendarInterval": {"Hour": 18, "Minute": 0}, "RunAtLoad": False, "ProcessType": "Background",
        "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
    }
    temporary = path.with_suffix(".plist.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ProjectError("refusing existing scheduler temporary path")
    temporary.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True))
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(path)],
                            capture_output=True, text=True, check=False, timeout=15)
    if result.returncode:
        path.unlink(missing_ok=True)
        raise ProjectError("launchd rejected the notification scheduler")
    return scheduler_status(project)


def uninstall_scheduler(project: Path) -> dict[str, Any]:
    path = _path(project)
    if path.is_symlink():
        raise ProjectError("refusing unsafe LaunchAgents path")
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{_label(project)}"],
                   capture_output=True, text=True, check=False, timeout=15)
    if path.exists():
        path.unlink()
    return scheduler_status(project)
