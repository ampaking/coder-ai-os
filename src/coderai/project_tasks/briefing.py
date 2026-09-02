"""Bounded, evidence-linked project orientation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from coderai.project_tasks.project import load_settings
from coderai.project_tasks.storage import list_tasks

MAX_FILE_BYTES = 64_000


def _read(project: Path, relative: str) -> str:
    target = project / relative
    if not target.is_file() or target.is_symlink():
        return ""
    with target.open("rb") as handle:
        return handle.read(MAX_FILE_BYTES).decode("utf-8", errors="replace")


def _first_heading_and_paragraph(markdown: str) -> tuple[str, str]:
    title = ""
    paragraph: list[str] = []
    after_title = False
    in_code = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not title and line.startswith("# "):
            title = line[2:].strip()[:240]
            after_title = True
            continue
        if after_title and line and not line.startswith(("#", "[", "!", "<", "- ", "* ")):
            paragraph.append(line)
            if sum(map(len, paragraph)) >= 500:
                break
        elif paragraph and not line:
            break
    purpose = " ".join(paragraph)
    purpose = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", purpose)
    purpose = purpose.replace("**", "").replace("__", "").replace("`", "")
    return title, purpose[:600]


def _project_yaml(project: Path) -> tuple[str, list[str]]:
    text = _read(project, ".coder-ai/project.yaml")
    project_id = project.name
    match = re.search(r"(?m)^\s*id:\s*['\"]?([^'\"#\n]+)", text)
    if match:
        project_id = match.group(1).strip()[:120]
    languages: list[str] = []
    match = re.search(r"(?m)^\s*languages:\s*\[([^]]*)]", text)
    if match:
        languages = [part.strip(" '\"")[:80] for part in match.group(1).split(",") if part.strip(" '\"")]
    return project_id, languages[:12]


def _validation_commands(project: Path) -> list[str]:
    standards = _read(project, ".ai/standards.md")
    commands: list[str] = []
    for line in standards.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and any(word in stripped.lower() for word in ("test", "lint", "typecheck", "build")):
            commands.append(stripped[2:][:240])
        if len(commands) >= 6:
            break
    return commands


def _project_flow(project: Path) -> str:
    navigator = _read(project, ".ai/PROJECT_NAVIGATOR.md")
    section = re.search(r"## Current relevant flow\s+```(?:text)?\s*(.*?)```", navigator, re.DOTALL)
    if not section:
        return ""
    return section.group(1).strip()[:1200]


def _entry_points(project: Path) -> list[str]:
    snapshot = _read(project, ".ai/PROJECT_SNAPSHOT.md")
    block = re.search(r"## Top-level structure.*?```\s*(.*?)```", snapshot, re.DOTALL)
    if not block:
        return []
    values: list[str] = []
    for line in block.group(1).splitlines():
        value = line.strip()
        if value and "/" not in value and value != "." and not value.startswith("."):
            values.append(value[:160])
        if len(values) >= 8:
            break
    return values


def build_briefing(project: Path, language: str | None = None, depth: str = "quick") -> dict[str, Any]:
    if depth not in {"quick", "working", "deep"}:
        raise ValueError("depth must be quick, working, or deep")
    settings = load_settings(project) or {}
    requested_language = language or str(settings.get("language", "auto"))
    readme = _read(project, "README.md")
    title, purpose = _first_heading_and_paragraph(readme)
    project_id, languages = _project_yaml(project)
    tasks = list_tasks(project)
    active = [task for task in tasks if task["status"] in {"active", "blocked", "needs_validation"}][:8]
    evidence = []
    for relative in (
        "README.md", ".coder-ai/project.yaml", ".ai/PROJECT_SNAPSHOT.md",
        ".ai/PROJECT_NAVIGATOR.md", ".ai/standards.md",
    ):
        target = project / relative
        if target.is_file() and not target.is_symlink():
            evidence.append({"path": relative, "modifiedAt": target.stat().st_mtime_ns})
    technical_terms = list(dict.fromkeys(re.findall(r"`([^`]{1,80})`", readme)))[:12]
    result = {
        "project": {"id": project_id, "title": title or project_id, "purpose": purpose},
        "languages": languages,
        "requestedLanguage": requested_language,
        "translationRequired": requested_language not in {"", "auto"},
        "entryPoints": _entry_points(project),
        "flow": _project_flow(project),
        "validation": _validation_commands(project),
        "activeTasks": [
            {"id": task["id"], "title": task["title"], "status": task["status"], "updatedAt": task["updated_at"]}
            for task in active
        ],
        "nextAction": (
            "Resolve the current blocker." if any(task["status"] == "blocked" for task in active)
            else "Complete the missing validation." if any(task["status"] == "needs_validation" for task in active)
            else "Continue the current active task." if active
            else "Choose one small task and define its proof."
        ),
        "evidence": evidence,
        "depth": depth,
        "technicalTerms": technical_terms,
        "readingPath": [
            {"level": "quick", "time": "30 seconds", "focus": "purpose, active work, next action"},
            {"level": "working", "time": "5 minutes", "focus": "flow, validation, entry points"},
            {"level": "deep", "time": "as needed", "focus": "evidence files and project maps"},
        ],
    }
    result["selected"] = {
        "quick": {"project": result["project"], "nextAction": result["nextAction"], "activeTasks": result["activeTasks"]},
        "working": {"flow": result["flow"], "validation": result["validation"], "entryPoints": result["entryPoints"]},
        "deep": {"evidence": result["evidence"], "technicalTerms": technical_terms},
    }[depth]
    return result
