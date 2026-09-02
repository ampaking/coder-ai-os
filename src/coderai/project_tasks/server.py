"""On-demand localhost dashboard for Project Tasks."""

from __future__ import annotations

import json
import os
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import fcntl

from coderai.project_tasks.briefing import build_briefing
from coderai.project_tasks.agent_runner import agent_capabilities, launch_agent
from coderai.project_tasks.coaching import explain_project
from coderai.project_tasks import SCHEMA_VERSION
from coderai.project_tasks.analytics import agent_sessions, evidence_calendar, period_insights, project_intelligence, recent_activity, recommendations, safe_export, set_idea_status, task_evidence_summaries, task_graph, temporal_map, weekly_review
from coderai.project_tasks.git_metadata import list_git_links, set_git_link
from coderai.project_tasks.notifications import analyze_notifications, list_notifications, notification_counts, set_notification_state
from coderai.project_tasks.scheduler import install_scheduler, scheduler_status, uninstall_scheduler
from coderai.project_tasks.project import ProjectError, load_settings, reject_symlinks, root_hash, state_dir, write_settings
from coderai.project_tasks.storage import StorageError, get_task, list_tasks, new_id, record_event

UI_DIR = Path(__file__).parent / "ui"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/accessibility.css": ("accessibility.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/actions.js": ("actions.js", "text/javascript; charset=utf-8"),
    "/actions.css": ("actions.css", "text/css; charset=utf-8"),
    "/responsive.css": ("responsive.css", "text/css; charset=utf-8"),
    "/clarity.css": ("clarity.css", "text/css; charset=utf-8"),
    "/insights.js": ("insights.js", "text/javascript; charset=utf-8"),
}


def overview(project: Path, include_activity: bool = True) -> dict[str, Any]:
    tasks = task_evidence_summaries(project)
    intelligence = project_intelligence(project)
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task["status"]] = counts.get(task["status"], 0) + 1
    active = [task for task in tasks if task["status"] in {"active", "blocked", "needs_validation"}]
    return {
        "counts": counts,
        "active": active[:8],
        "tasks": tasks,
        "briefing": build_briefing(project),
        "graph": task_graph(project, intelligence=intelligence),
        "intelligence": intelligence,
        "review": weekly_review(project),
        "ideas": recommendations(project),
        "sessions": agent_sessions(project),
        "activity": recent_activity(project) if include_activity else [],
    }


def public_settings(project: Path) -> dict[str, Any]:
    settings = load_settings(project) or {}
    return {"enabled": bool(settings.get("enabled")),
            "automaticCollection": bool(settings.get("automaticCollection")),
            "gitMetadata": bool(settings.get("gitMetadata")),
            "notificationsEnabled": bool(settings.get("notificationsEnabled")),
            "language": str(settings.get("language", "auto")),
            "retentionDays": int(settings.get("retentionDays", 90)),
            "theme": str(settings.get("theme", "black")),
            "timeZone": str(settings.get("timeZone", "local"))}


def update_public_settings(project: Path, payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings(project) or {}
    if "retentionDays" in payload:
        retention = int(payload["retentionDays"])
        if not 1 <= retention <= 3650:
            raise StorageError("retentionDays must be between 1 and 3650")
        settings["retentionDays"] = retention
    if "language" in payload:
        language = str(payload["language"]).strip()
        if not language or len(language) > 80:
            raise StorageError("language must be between 1 and 80 characters")
        settings["language"] = language
    if "gitMetadata" in payload:
        if not isinstance(payload["gitMetadata"], bool):
            raise StorageError("gitMetadata must be true or false")
        settings["gitMetadata"] = payload["gitMetadata"]
    if "notificationsEnabled" in payload:
        if not isinstance(payload["notificationsEnabled"], bool):
            raise StorageError("notificationsEnabled must be true or false")
        settings["notificationsEnabled"] = payload["notificationsEnabled"]
    if "timeZone" in payload:
        timezone = str(payload["timeZone"]).strip()
        if not timezone or len(timezone) > 80:
            raise StorageError("timeZone must be between 1 and 80 characters")
        if timezone != "local":
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError as exc:
                raise StorageError("unknown IANA timezone") from exc
        settings["timeZone"] = timezone
    settings.update({"projectHash": root_hash(project), "schemaVersion": SCHEMA_VERSION})
    write_settings(project, settings)
    return public_settings(project)


def make_handler(project: Path, access_token: str, instance_id: str = "",
                 control_token: str = "") -> type[BaseHTTPRequestHandler]:
    class TasksHandler(BaseHTTPRequestHandler):
        server_version = "ProjectTasks/1"

        def log_message(self, format_string: str, *args: object) -> None:
            return

        def _headers(self, status: int, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'sha256-IfHJzjY/85THBkRGx4e5nSGnu1MyQiy2pf2mu/r/W+U='; style-src 'self' 'sha256-5YpWeplbqmgz7BhRswBFhXdu2vn6enFq7XJgaG0vp6A='; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()

        def _host_allowed(self) -> bool:
            host = self.headers.get("Host", "").split(":", 1)[0].strip("[]")
            return host in {"127.0.0.1", "localhost", "::1"}

        def _token(self) -> str:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            return query.get("token", [self.headers.get("X-Project-Tasks-Token", "")])[0]

        def _authorized(self) -> bool:
            return self._host_allowed() and secrets.compare_digest(self._token(), access_token)

        def _json(self, value: Any, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            self._headers(status, "application/json; charset=utf-8")
            self.wfile.write(body)

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            if route == "/health" and self._host_allowed():
                health = {"status": "ready"}
                if instance_id:
                    health["instanceId"] = instance_id
                self._json(health)
                return
            if not self._authorized():
                self._error(HTTPStatus.FORBIDDEN, "invalid local session")
                return
            try:
                if route in ASSETS:
                    filename, content_type = ASSETS[route]
                    body = (UI_DIR / filename).read_bytes()
                    if filename == "index.html":
                        body = body.replace(b"__PROJECT_TASKS_TOKEN__", access_token.encode())
                    self._headers(HTTPStatus.OK, content_type)
                    self.wfile.write(body)
                    return
                if route == "/api/overview":
                    query = urllib.parse.parse_qs(parsed.query)
                    self._json(overview(project, query.get("activity", ["1"])[0] != "0"))
                    return
                if route == "/api/insights":
                    query = urllib.parse.parse_qs(parsed.query)
                    period = query.get("period", ["week"])[0]
                    anchor = query.get("anchor", [None])[0]
                    time_zone = query.get("timeZone", [None])[0]
                    self._json(period_insights(project, period, anchor, time_zone))
                    return
                if route == "/api/map":
                    query = urllib.parse.parse_qs(parsed.query)
                    period = query.get("period", ["week"])[0]
                    anchor = query.get("anchor", [None])[0]
                    time_zone = query.get("timeZone", [None])[0]
                    self._json({"graph": temporal_map(project, period, anchor, time_zone),
                                "calendar": evidence_calendar(project, time_zone)})
                    return
                if route == "/api/tasks":
                    self._json(list_tasks(project))
                    return
                if route == "/api/brief":
                    self._json(build_briefing(project))
                    return
                if route == "/api/settings":
                    self._json(public_settings(project))
                    return
                if route == "/api/export":
                    self._json(safe_export(project))
                    return
                if route == "/api/git-links":
                    self._json(list_git_links(project))
                    return
                if route == "/api/notifications":
                    query = urllib.parse.parse_qs(parsed.query)
                    limit = int(query.get("limit", ["50"])[0])
                    items = list_notifications(project, limit)
                    counts = notification_counts(project)
                    self._json({"items": items, "unreadCount": counts["unread"], "counts": counts,
                                "scheduler": scheduler_status(project)})
                    return
                if route.startswith("/api/tasks/"):
                    task_id = urllib.parse.unquote(route.removeprefix("/api/tasks/"))
                    task = get_task(project, task_id)
                    if task is None:
                        self._error(HTTPStatus.NOT_FOUND, "task not found")
                    else:
                        self._json(task)
                    return
                self._error(HTTPStatus.NOT_FOUND, "not found")
            except (OSError, ProjectError, StorageError, ValueError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/shutdown":
                supplied = self.headers.get("X-Project-Tasks-Instance", "")
                if not self._host_allowed() or not control_token or not secrets.compare_digest(supplied, control_token):
                    self._error(HTTPStatus.FORBIDDEN, "invalid local control session")
                    return
                self._json({"status": "stopping"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if not self._authorized():
                self._error(HTTPStatus.FORBIDDEN, "invalid local session")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8_192:
                    raise StorageError("invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise StorageError("request must be an object")
                if parsed.path == "/api/settings":
                    self._json(update_public_settings(project, payload))
                    return
                if parsed.path == "/api/notifications/run":
                    items = analyze_notifications(project)
                    counts = notification_counts(project)
                    self._json({"items": items, "unreadCount": counts["unread"], "counts": counts,
                                "scheduler": scheduler_status(project)})
                    return
                if parsed.path == "/api/notifications/state":
                    self._json(set_notification_state(project, str(payload.get("notificationId", "")),
                                                      str(payload.get("state", ""))))
                    return
                if parsed.path == "/api/notifications/scheduler":
                    action = str(payload.get("action", ""))
                    if action == "install":
                        result = install_scheduler(project, int(payload.get("interval", 1800)))
                        update_public_settings(project, {"notificationsEnabled": True})
                        self._json(result)
                    elif action == "uninstall":
                        result = uninstall_scheduler(project)
                        update_public_settings(project, {"notificationsEnabled": False})
                        self._json(result)
                    else:
                        raise StorageError("notification scheduler action must be install or uninstall")
                    return
                if parsed.path == "/api/ideas/status":
                    self._json(set_idea_status(project, str(payload.get("ideaId", "")), str(payload.get("status", ""))))
                    return
                if parsed.path == "/api/git-links/status":
                    self._json(set_git_link(project, str(payload.get("taskId", "")),
                                            str(payload.get("commitHash", "")), str(payload.get("status", ""))))
                    return
                if parsed.path == "/api/coach":
                    self._json({
                        **explain_project(project, payload.get("question", ""),
                                          str(payload.get("period", "week")),
                                          str(payload["anchor"]) if payload.get("anchor") else None),
                        "agents": agent_capabilities(project),
                    })
                    return
                if parsed.path == "/api/coach/run":
                    self._json(launch_agent(project, str(payload.get("provider", "")),
                                            payload.get("question", ""), str(payload.get("period", "week")),
                                            str(payload["anchor"]) if payload.get("anchor") else None),
                               HTTPStatus.ACCEPTED)
                    return
                if parsed.path != "/api/corrections":
                    self._error(HTTPStatus.NOT_FOUND, "not found")
                    return
                task_id = str(payload.get("taskId", ""))
                task = get_task(project, task_id)
                if task is None:
                    raise StorageError("task not found")
                result = record_event(project, {
                    "eventId": new_id("event"),
                    "type": "task_corrected",
                    "summary": "Task corrected from the local dashboard",
                    "task": {"id": task_id, "title": task["title"]},
                    "session": {"id": new_id("session"), "agentName": "project-tasks-ui"},
                    "correction": {
                        "field": payload.get("field"), "value": payload.get("value"),
                        "reason": payload.get("reason", ""),
                    },
                })
                self._json(result, HTTPStatus.CREATED)
            except (json.JSONDecodeError, ProjectError, StorageError, ValueError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))

    return TasksHandler


def _dashboard_state_path(project: Path) -> Path:
    path = state_dir(project) / "dashboard.json"
    reject_symlinks(project, path)
    return path


def _acquire_dashboard_lock(project: Path) -> int:
    directory = state_dir(project)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "dashboard.lock"
    reject_symlinks(project, path)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    os.chmod(path, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise StorageError("a Project Tasks dashboard is already running; close it before opening another")
    return descriptor


def _write_dashboard_state(project: Path, value: dict[str, Any]) -> None:
    path = _dashboard_state_path(project)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(".dashboard.tmp")
    reject_symlinks(project, temporary)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _running_dashboard(project: Path) -> dict[str, Any] | None:
    path = _dashboard_state_path(project)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        port = int(state["port"])
        instance_id = str(state["instanceId"])
        control_token = str(state["controlToken"])
        if (state.get("projectHash") != root_hash(project) or not instance_id
                or not control_token or not 0 < port < 65536):
            raise ValueError("invalid dashboard state")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            health = json.load(response)
        if health.get("status") != "ready" or health.get("instanceId") != instance_id:
            raise ValueError("dashboard identity mismatch")
        return state
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None


def close_dashboard(project: Path) -> bool:
    state = _running_dashboard(project)
    if state is None:
        return False
    try:
        port = int(state["port"])
        control_token = str(state["controlToken"])
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/shutdown", method="POST",
            headers={"X-Project-Tasks-Instance": control_token}, data=b"",
        )
        with urllib.request.urlopen(request, timeout=1) as response:
            if json.load(response).get("status") != "stopping":
                raise ValueError("dashboard did not accept shutdown")
        return True
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        _dashboard_state_path(project).unlink(missing_ok=True)
        return False


def serve(project: Path, port: int = 0, launch_browser: bool = True, access_token: str | None = None,
          control_project: Path | None = None) -> None:
    access_token = access_token or secrets.token_urlsafe(32)
    if len(access_token) < 16:
        raise StorageError("local access token must be at least 16 characters")
    control_project = control_project or project
    lock_descriptor = _acquire_dashboard_lock(control_project)
    if _running_dashboard(control_project) is not None:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)
        raise StorageError("a Project Tasks dashboard is already running; close it before opening another")
    instance_id = secrets.token_urlsafe(18)
    control_token = secrets.token_urlsafe(32)
    try:
        server = ThreadingHTTPServer(
            ("127.0.0.1", port), make_handler(project, access_token, instance_id, control_token),
        )
        server.daemon_threads = True
        _write_dashboard_state(control_project, {
            "instanceId": instance_id,
            "controlToken": control_token,
            "port": server.server_port,
            "projectHash": root_hash(control_project),
        })
    except BaseException:
        if "server" in locals():
            server.server_close()
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)
        raise
    url = f"http://127.0.0.1:{server.server_port}/?token={access_token}"
    print(f"Project Tasks: {url}", flush=True)
    print("Stop with Ctrl-C or: coder-ai-os tasks close", flush=True)
    if launch_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state_path = _dashboard_state_path(control_project)
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("instanceId") == instance_id:
                state_path.unlink()
        except (OSError, json.JSONDecodeError):
            pass
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)
