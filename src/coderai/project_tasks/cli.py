#!/usr/bin/env python3
"""CLI entry point for Project Tasks."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from coderai.project_tasks import SCHEMA_VERSION, SETTINGS_VERSION
from coderai.project_tasks.briefing import build_briefing
from coderai.project_tasks.demo import seed_demo
from coderai.project_tasks.adapters import ingest_lifecycle
from coderai.project_tasks.analytics import recommendations, safe_export, set_idea_status, weekly_review
from coderai.project_tasks.git_metadata import list_git_links, scan_git, set_git_link
from coderai.project_tasks.hook_collector import MAX_HOOK_BYTES, capture_hook, capture_validation_result
from coderai.project_tasks.notifications import analyze_notifications, deliver_notifications, list_notifications
from coderai.project_tasks.scheduler import install_scheduler, scheduler_status, uninstall_scheduler
from coderai.project_tasks.project import (
    ProjectError,
    load_settings,
    reject_symlinks,
    require_identity,
    resolve_project,
    root_hash,
    settings_path,
    state_dir,
    write_settings,
)
from coderai.project_tasks.prompts import shape_request
from coderai.project_tasks.storage import StorageError, cleanup, database_health, get_task, initialize, list_tasks, record_event, repair_database
from coderai.project_tasks.server import close_dashboard, serve


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _defaults(project: Path, enabled: bool) -> dict[str, Any]:
    return {
        "automaticCollection": enabled,
        "enabled": enabled,
        "gitMetadata": False,
        "language": "auto",
        "notificationsEnabled": False,
        "projectHash": root_hash(project),
        "retentionDays": 90,
        "schemaVersion": SCHEMA_VERSION,
        "settingsVersion": SETTINGS_VERSION,
        "theme": "black",
        "timeZone": "local",
        "updatedAt": _now(),
    }


def _project(value: str) -> Path:
    project = resolve_project(value)
    require_identity(project)
    return project


def command_enable(project: Path, _args: argparse.Namespace) -> int:
    settings = load_settings(project) or _defaults(project, True)
    timestamp = _now()
    settings.update({"automaticCollection": True, "automaticCollectionEnabledAt": timestamp,
                     "enabled": True, "projectHash": root_hash(project), "updatedAt": timestamp})
    write_settings(project, settings)
    initialize(project)
    settings["schemaVersion"] = SCHEMA_VERSION
    write_settings(project, settings)
    print("Project Tasks and content-free automatic collection enabled for this project.")
    return 0


def command_disable(project: Path, _args: argparse.Namespace) -> int:
    settings = load_settings(project) or _defaults(project, False)
    settings.update({"automaticCollection": False, "enabled": False,
                     "projectHash": root_hash(project), "updatedAt": _now()})
    write_settings(project, settings)
    print("Project Tasks disabled. Existing local data was preserved.")
    return 0


def command_status(project: Path, args: argparse.Namespace) -> int:
    settings = load_settings(project)
    result = {
        "configured": settings is not None,
        "enabled": bool(settings and settings.get("enabled")),
        "project": project.name,
        "stateDir": str(state_dir(project)),
    }
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif not result["configured"]:
        print("Project Tasks is available but not configured.")
    else:
        print(f"Project Tasks: {'enabled' if result['enabled'] else 'disabled'}")
        print(f"Local state: {result['stateDir']}")
    return 0


def command_delete(project: Path, args: argparse.Namespace) -> int:
    directory = state_dir(project)
    reject_symlinks(project, directory)
    if not directory.exists():
        print("No Project Tasks data exists for this project.")
        return 0
    if not directory.is_dir():
        raise ProjectError(f"state target is not a directory: {directory}")
    if not args.yes:
        if not sys.stdin.isatty():
            raise ProjectError("delete requires --yes in non-interactive mode")
        answer = input(f"Delete all local Project Tasks data for {project.name}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Delete cancelled.")
            return 0
    expected = project / ".coder-ai" / "tasks"
    if directory != expected:
        raise ProjectError("refusing unexpected delete target")
    shutil.rmtree(directory)
    print("Deleted local Project Tasks data.")
    return 0


def command_record(project: Path, args: argparse.Namespace) -> int:
    if args.file:
        if args.file == "-":
            payload = json.load(sys.stdin)
        else:
            payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    else:
        payload = json.loads(args.json)
    result = record_event(project, payload)
    print(json.dumps(result, sort_keys=True))
    return 0


def command_cleanup(project: Path, _args: argparse.Namespace) -> int:
    removed = cleanup(project)
    print(f"Removed {removed} expired task events.")
    return 0


def command_list(project: Path, args: argparse.Namespace) -> int:
    tasks = list_tasks(project, args.status)
    if args.json:
        print(json.dumps(tasks, sort_keys=True))
        return 0
    if not tasks:
        print("No tasks recorded.")
        return 0
    for item in tasks:
        print(f"{item['id']}  {item['status']:<16}  {item['title']}")
    return 0


def command_show(project: Path, args: argparse.Namespace) -> int:
    task = get_task(project, args.task_id)
    if task is None:
        raise StorageError(f"task not found: {args.task_id}")
    print(json.dumps(task, indent=2, sort_keys=True))
    return 0


def command_shape(_project: Path, args: argparse.Namespace) -> int:
    print(json.dumps(shape_request(args.text), indent=2, sort_keys=True))
    return 0


def command_brief(project: Path, args: argparse.Namespace) -> int:
    briefing = build_briefing(project, args.language, args.depth)
    print(json.dumps(briefing, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def command_config(project: Path, args: argparse.Namespace) -> int:
    settings = load_settings(project) or _defaults(project, False)
    if args.language is not None:
        settings["language"] = args.language
    if args.retention_days is not None:
        if not 1 <= args.retention_days <= 3650:
            raise ProjectError("retention days must be between 1 and 3650")
        settings["retentionDays"] = args.retention_days
    if args.git_metadata is not None:
        settings["gitMetadata"] = args.git_metadata == "on"
    if args.timezone is not None:
        if args.timezone != "local":
            try:
                ZoneInfo(args.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ProjectError("unknown IANA timezone") from exc
        settings["timeZone"] = args.timezone
    settings["updatedAt"] = _now()
    write_settings(project, settings)
    print(json.dumps(settings, indent=2, sort_keys=True))
    return 0


def command_collect(project: Path, args: argparse.Namespace) -> int:
    settings = load_settings(project) or _defaults(project, False)
    if args.action == "enable":
        if not settings.get("enabled"):
            raise ProjectError("enable Project Tasks before enabling automatic collection")
        settings["automaticCollection"] = True
        settings["automaticCollectionEnabledAt"] = _now()
    elif args.action == "disable":
        settings["automaticCollection"] = False
    if args.action != "status":
        settings.update({"projectHash": root_hash(project), "updatedAt": _now()})
        settings.setdefault("schemaVersion", SCHEMA_VERSION)
        write_settings(project, settings)
    result = {
        "enabled": bool(settings.get("automaticCollection")),
        "gitMetadata": bool(settings.get("gitMetadata")),
        "mode": "event-hooks" if settings.get("automaticCollection") else "off",
        "policy": {
            "automatic": ["agent lifecycle", "validation outcomes"],
            "optional": ["content-free Git metadata"],
            "excluded": ["prompts", "model responses", "source contents", "file paths",
                         "terminal output", "keystrokes", "person scoring"],
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def command_notify(project: Path, args: argparse.Namespace) -> int:
    settings = load_settings(project) or _defaults(project, True)
    if args.action in {"enable", "disable"}:
        settings["notificationsEnabled"] = args.action == "enable"
        settings["updatedAt"] = _now()
        write_settings(project, settings)
    if args.action == "run":
        generated = analyze_notifications(project)
        delivered = deliver_notifications(project) if args.deliver else 0
        print(json.dumps({"generated": len(generated), "delivered": delivered}, sort_keys=True))
        return 0
    if args.action == "list":
        print(json.dumps(list_notifications(project), indent=2, sort_keys=True))
        return 0
    if args.action == "install":
        if not settings.get("notificationsEnabled"):
            raise ProjectError("enable notifications before installing the scheduler")
        print(json.dumps(install_scheduler(project, args.interval), sort_keys=True))
        return 0
    if args.action == "uninstall":
        print(json.dumps(uninstall_scheduler(project), sort_keys=True))
        return 0
    print(json.dumps({"enabled": bool(settings.get("notificationsEnabled")),
                      "scheduler": scheduler_status(project), "mode": "scheduled-one-shot"}, sort_keys=True))
    return 0


def command_hook(project: Path, args: argparse.Namespace) -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
        result = capture_hook(project, args.provider, raw)
    except BaseException as exc:
        result = {"status": "failed", "captured": False, "reason": type(exc).__name__}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    return 0


def command_validation_result(project: Path, args: argparse.Namespace) -> int:
    result = capture_validation_result(project, args.collector, args.run_id, args.outcome)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    return 0


def command_open(project: Path, args: argparse.Namespace) -> int:
    if args.demo:
        with tempfile.TemporaryDirectory(prefix="project-tasks-demo-") as directory:
            demo_project = Path(directory).resolve()
            seed_demo(demo_project)
            print("Demo preview: disposable example data; your project database is unchanged.", flush=True)
            serve(demo_project, port=args.port, launch_browser=not args.no_browser,
                  access_token=args.access_token, control_project=project)
        return 0
    serve(project, port=args.port, launch_browser=not args.no_browser, access_token=args.access_token)
    return 0


def command_close(project: Path, _args: argparse.Namespace) -> int:
    if close_dashboard(project):
        print("Stopped the Project Tasks dashboard.")
    else:
        print("No running Project Tasks dashboard was found for this project.")
    return 0


def command_event(project: Path, args: argparse.Namespace) -> int:
    data = json.loads(args.json)
    print(json.dumps(ingest_lifecycle(project, data, args.action), sort_keys=True))
    return 0


def command_git(project: Path, args: argparse.Namespace) -> int:
    print(json.dumps(scan_git(project, args.limit), sort_keys=True))
    return 0


def command_git_links(project: Path, _args: argparse.Namespace) -> int:
    print(json.dumps(list_git_links(project), indent=2, sort_keys=True))
    return 0


def command_git_link(project: Path, args: argparse.Namespace) -> int:
    print(json.dumps(set_git_link(project, args.task_id, args.commit_hash, args.status), indent=2, sort_keys=True))
    return 0


def command_review(project: Path, args: argparse.Namespace) -> int:
    print(json.dumps(weekly_review(project, args.days), indent=2, sort_keys=True))
    return 0


def command_ideas(project: Path, _args: argparse.Namespace) -> int:
    print(json.dumps(recommendations(project), indent=2, sort_keys=True))
    return 0


def command_idea(project: Path, args: argparse.Namespace) -> int:
    print(json.dumps(set_idea_status(project, args.idea_id, args.status), indent=2, sort_keys=True))
    return 0


def command_export(project: Path, args: argparse.Namespace) -> int:
    output = json.dumps(safe_export(project), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output == "-":
        print(output, end="")
    else:
        target = Path(args.output).resolve()
        if target.is_symlink():
            raise ProjectError("refusing to export through a symlink")
        target.write_text(output, encoding="utf-8")
    return 0


def command_doctor(project: Path, _args: argparse.Namespace) -> int:
    health = database_health(project)
    print(json.dumps(health, sort_keys=True))
    return 0 if health["healthy"] else 1


def command_repair(project: Path, args: argparse.Namespace) -> int:
    if not args.yes:
        raise ProjectError("repair requires --yes; the unreadable database is preserved as a backup")
    print(json.dumps(repair_database(project), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coder-ai tasks")
    parser.add_argument("--project", default=".", help=argparse.SUPPRESS)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("enable")
    subcommands.add_parser("disable")
    status = subcommands.add_parser("status")
    status.add_argument("--json", action="store_true")
    delete = subcommands.add_parser("delete")
    delete.add_argument("--yes", action="store_true")
    record = subcommands.add_parser("record")
    source = record.add_mutually_exclusive_group(required=True)
    source.add_argument("--json")
    source.add_argument("--file")
    subcommands.add_parser("cleanup")
    listing = subcommands.add_parser("list")
    listing.add_argument("--status")
    listing.add_argument("--json", action="store_true")
    show = subcommands.add_parser("show")
    show.add_argument("task_id")
    shape = subcommands.add_parser("shape")
    shape.add_argument("text")
    brief = subcommands.add_parser("brief")
    brief.add_argument("--language")
    brief.add_argument("--depth", choices=("quick", "working", "deep"), default="quick")
    config = subcommands.add_parser("config")
    config.add_argument("--language")
    config.add_argument("--retention-days", type=int)
    config.add_argument("--git-metadata", choices=("on", "off"))
    config.add_argument("--timezone")
    collect = subcommands.add_parser("collect", help="control trusted automatic project collection")
    collect.add_argument("action", choices=("enable", "disable", "status"))
    notify = subcommands.add_parser("notify", help="analyze and deliver local project notifications")
    notify.add_argument("action", choices=("enable", "disable", "status", "run", "list", "install", "uninstall"))
    notify.add_argument("--deliver", action="store_true", help="show eligible native notifications")
    notify.add_argument("--interval", type=int, default=1800, help="scheduler interval in seconds (300-86400)")
    hook = subcommands.add_parser("hook", help=argparse.SUPPRESS)
    hook.add_argument("provider", choices=("claude",))
    hook.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    validation_result = subcommands.add_parser("validation-result", help=argparse.SUPPRESS)
    validation_result.add_argument("--collector", required=True, choices=("val",))
    validation_result.add_argument("--run-id", required=True)
    validation_result.add_argument("--outcome", required=True, choices=("passed", "failed", "warning"))
    validation_result.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    open_command = subcommands.add_parser("open")
    open_command.add_argument("--port", type=int, default=0)
    open_command.add_argument("--no-browser", action="store_true")
    open_command.add_argument("--demo", action="store_true", help="preview disposable example data")
    open_command.add_argument("--access-token", help=argparse.SUPPRESS)
    subcommands.add_parser("close", help="stop this project's Project Tasks dashboard")
    event = subcommands.add_parser("event", help="record a portable agent lifecycle event")
    event.add_argument("action", choices=("start", "continue", "pause", "block", "validate", "fail", "complete", "end"))
    event.add_argument("--json", required=True, help="structured summary; raw prompts are rejected")
    git_command = subcommands.add_parser("git", help="collect optional content-free Git metadata")
    git_command.add_argument("--limit", type=int, default=200)
    subcommands.add_parser("git-links")
    git_link = subcommands.add_parser("git-link")
    git_link.add_argument("--task-id", required=True)
    git_link.add_argument("--commit-hash", required=True)
    git_link.add_argument("--status", required=True, choices=("candidate", "confirmed", "rejected"))
    review = subcommands.add_parser("review")
    review.add_argument("--days", type=int, default=7)
    subcommands.add_parser("ideas")
    idea = subcommands.add_parser("idea")
    idea.add_argument("idea_id")
    idea.add_argument("--status", required=True, choices=("proposed", "accepted", "rejected", "planned", "completed"))
    export = subcommands.add_parser("export")
    export.add_argument("--output", default="-")
    subcommands.add_parser("doctor")
    repair = subcommands.add_parser("repair")
    repair.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        project = _project(args.project)
        command = globals()[f"command_{args.command.replace('-', '_')}"]
        return int(command(project, args))
    except (ProjectError, StorageError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"coder-ai tasks: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
