#!/usr/bin/env python3
"""On-demand localhost dashboard security and API tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime
from unittest import mock
from pathlib import Path

from coderai.project_tasks.analytics import _source_status, project_intelligence, task_graph
from coderai.project_tasks.server import make_handler
from coderai.project_tasks.storage import connect
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "coderai" / "project_tasks" / "cli.py"


class TasksServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name) / "project"
        (base / ".coder-ai").mkdir(parents=True)
        self.project = base.resolve()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        digest = hashlib.sha256(str(self.project).encode()).hexdigest()
        (self.project / ".coder-ai" / "identity.json").write_text(json.dumps({
            "project_id": "project", "git_root_hash": f"sha256:{digest}", "remote_hash": "sha256:test"
        }))
        (self.project / ".coder-ai" / "project.yaml").write_text("project:\n  id: dashboard-test\n")
        (self.project / "README.md").write_text("# Dashboard Test\n\nA local project dashboard.\n")
        self.run_cli("enable")
        self.run_cli("record", "--json", json.dumps({
            "type": "task_started", "summary": "Build dashboard",
            "task": {"id": "task-ui", "title": "Build dashboard", "theme": "Project Tasks"},
            "session": {"id": "session-ui", "agentName": "codex"},
        }))
        self.token = "test-token"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.project, self.token))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), "--project", str(self.project), *arguments],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def get(self, route: str) -> urllib.response.addinfourl:
        return urllib.request.urlopen(f"{self.base}{route}", timeout=3)

    def post(self, route: str, payload: dict[str, object]) -> urllib.response.addinfourl:
        request = urllib.request.Request(
            f"{self.base}{route}?token={self.token}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        return urllib.request.urlopen(request, timeout=3)

    def test_requires_token_and_serves_no_external_assets(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.get("/api/overview")
        self.assertEqual(failure.exception.code, 403)
        failure.exception.close()
        response = self.get(f"/?token={self.token}")
        html = response.read().decode()
        self.assertIn("Project Tasks", html)
        self.assertNotIn("https://", html)
        self.assertIn("actions.js", html)
        self.assertIn("insights.js", html)
        self.assertIn("clarity.css", html)
        self.assertIn('id="guideToggle"', html)
        self.assertIn('aria-controls="guideContent"', html)
        self.assertIn('id="shortcutDialog"', html)
        self.assertIn('id="characterShortcuts"', html)
        self.assertIn('id="periodStatus"', html)
        self.assertIn('id="activitySummary"', html)
        self.assertIn('id="sessionSummary"', html)
        self.assertIn('id="taskStatus"', html)
        self.assertIn('id="taskSort"', html)
        self.assertIn('id="taskShowMore"', html)
        self.assertIn('id="correctionStatusValue"', html)
        self.assertIn('aria-keyshortcuts="?"', html)
        self.assertIn('aria-keyshortcuts="/"', html)
        self.assertIn('Reach anywhere quickly', html)
        insights = self.get(f"/insights.js?token={self.token}").read().decode()
        self.assertIn("button.setAttribute('aria-pressed'", insights)
        self.assertIn("NO MATCHING NODES", insights)
        self.assertIn("Evidence not connected", insights)
        self.assertIn("Loading bounded project evidence", insights)
        self.assertIn("classList.contains('active')", insights)
        self.assertIn("const finiteNumber", insights)
        self.assertIn("state.intelligence?.coverage?.caution", insights)
        self.assertIn("id=\"decisionSummary\"", insights)
        self.assertIn("resolvedOptions().timeZone", insights)
        self.assertIn("previous comparable period", insights)
        self.assertIn("normalizePeriodInsights", insights)
        self.assertIn("totals?.completedTasks ?? totals?.completed", insights)
        self.assertIn("inspectReviewBucket", insights)
        self.assertNotIn("document.querySelector('#tab-now').click(); inspectBucket", insights)
        self.assertIn("Agent not recorded", insights)
        self.assertIn("periodSessions", insights)
        self.assertIn("compatibility-active-task", insights)
        self.assertIn("without recorded agent identity", insights)
        self.assertIn("Needs a decision", insights)
        actions = self.get(f"/actions.js?token={self.token}").read().decode()
        self.assertIn("Observed time evidence", actions)
        self.assertIn("Reported effort", actions)
        self.assertIn("observed.caution", actions)
        self.assertIn("fallbackSpan", actions)
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_overview_contains_local_task(self) -> None:
        response = self.get(f"/api/overview?token={self.token}")
        data = json.loads(response.read())
        self.assertEqual(data["active"][0]["id"], "task-ui")
        self.assertEqual(data["counts"]["active"], 1)
        node_types = {node["type"] for node in data["graph"]["nodes"]}
        self.assertTrue({"task", "theme", "session"}.issubset(node_types))
        self.assertTrue(any(edge["type"] == "worked_on" for edge in data["graph"]["edges"]))
        self.assertTrue({"source", "signal", "recommendation"}.issubset(node_types))
        self.assertEqual(data["intelligence"]["mode"], "active")
        self.assertEqual(data["intelligence"]["recommendation"]["taskId"], "task-ui")
        self.assertTrue(any("excludes prompts" in item for item in data["intelligence"]["privacy"]))
        self.assertTrue(any(edge["type"] == "prioritizes" for edge in data["graph"]["edges"]))
        self.assertEqual(data["graph"]["contractVersion"], 1)
        node_ids = {node["id"] for node in data["graph"]["nodes"]}
        self.assertTrue(all(edge["source"] in node_ids and edge["target"] in node_ids
                            for edge in data["graph"]["edges"]))
        task_node = next(node for node in data["graph"]["nodes"] if node["type"] == "task")
        self.assertIn("updatedAt", task_node)
        self.assertEqual(data["activity"][0]["taskTitle"], "Build dashboard")
        self.assertEqual(data["tasks"][0]["latestEvent"], "task_started")
        self.assertGreaterEqual(len(data["review"]["days"]), 1)
        self.assertLessEqual(len(data["review"]["days"]), 7)
        self.assertIn("completedTasks", data["review"])

    @mock.patch("coderai.project_tasks.server.launch_agent")
    def test_coach_launch_returns_receipt_without_agent_output(self, launch: mock.Mock) -> None:
        launch.return_value = {
            "provider": "codex", "terminalOpened": True,
            "agentInitialized": "unknown-check-terminal", "mode": "interactive-read-only", "stored": False,
            "caution": "Missing data is unknown.", "redactions": [],
        }

        response = self.post("/api/coach/run", {"provider": "codex", "question": "What next?"})
        data = json.loads(response.read())

        self.assertEqual(response.status, 202)
        launch.assert_called_once_with(self.project, "codex", "What next?", "week", None)
        self.assertTrue(data["terminalOpened"])
        self.assertEqual(data["agentInitialized"], "unknown-check-terminal")
        self.assertNotIn("answer", data)

    def test_health_discloses_no_task_data_without_token(self) -> None:
        response = self.get("/health")
        self.assertEqual(json.loads(response.read()), {"status": "ready"})

    def test_shutdown_does_not_accept_public_instance_identity(self) -> None:
        handler = make_handler(self.project, self.token, "public-instance", "private-control")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/shutdown", data=b"", method="POST",
            headers={"X-Project-Tasks-Instance": "public-instance"},
        )
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(failure.exception.code, 403)
        failure.exception.close()

    def test_bounded_graph_keeps_recommendation_and_has_no_dangling_edges(self) -> None:
        graph = task_graph(self.project, limit=4)
        node_ids = {node["id"] for node in graph["nodes"]}
        self.assertTrue(any(node["type"] == "recommendation" for node in graph["nodes"]))
        self.assertTrue(all(edge["source"] in node_ids and edge["target"] in node_ids for edge in graph["edges"]))
        self.assertTrue(graph["bounds"]["truncated"])

    def test_source_freshness_and_proof_gap_provenance_are_honest(self) -> None:
        self.assertEqual(_source_status(1, "2020-01-01T00:00:00+00:00"), "stale")
        self.assertEqual(_source_status(1, datetime.now(UTC).isoformat()), "connected")
        self.assertEqual(_source_status(1, "2999-01-01T00:00:00+00:00"), "future-clock-skew")
        self.run_cli("record", "--json", json.dumps({
            "type": "completion_proposed", "summary": "Ready for proof",
            "task": {"id": "task-ui", "title": "Build dashboard"},
            "session": {"id": "session-proof", "agentName": "codex"},
        }))
        intelligence = project_intelligence(self.project)
        proof_gap = next(signal for signal in intelligence["signals"] if signal["kind"] == "proof-gap")
        self.assertEqual(proof_gap["sourceIds"], ["lifecycle"])

    def test_graph_lookup_indexes_exist(self) -> None:
        with connect(self.project) as connection:
            blocker_indexes = {row[1] for row in connection.execute("PRAGMA index_list(blockers)")}
            validation_indexes = {row[1] for row in connection.execute("PRAGMA index_list(validations)")}
        self.assertIn("blockers_task_status_time", blocker_indexes)
        self.assertIn("validations_task_time", validation_indexes)

    def test_stale_active_work_recommends_state_refresh(self) -> None:
        with connect(self.project) as connection:
            connection.execute("UPDATE task_events SET occurred_at='2020-01-01T00:00:00+00:00'")
            connection.execute("UPDATE tasks SET updated_at='2020-01-01T00:00:00+00:00'")
        intelligence = project_intelligence(self.project)
        self.assertEqual(intelligence["coverage"]["status"], "insufficient")
        self.assertEqual(intelligence["recommendation"]["id"], "next-state-refresh")
        self.assertEqual(intelligence["recommendation"]["signalIds"], ["coverage:lifecycle"])

    def test_idle_recommendation_remains_connected_to_evidence(self) -> None:
        for event_type, extra in (
            ("validation_passed", {"validation": {"category": "test", "commandSummary": "tests"}}),
            ("task_completed", {}),
        ):
            self.run_cli("record", "--json", json.dumps({
                "type": event_type, "summary": event_type,
                "task": {"id": "task-ui", "title": "Build dashboard"},
                "session": {"id": "session-idle", "agentName": "codex"}, **extra,
            }))
        intelligence = project_intelligence(self.project)
        self.assertEqual(intelligence["recommendation"]["id"], "next-opportunity-review")
        self.assertEqual(intelligence["recommendation"]["signalIds"], ["opportunity:idle"])
        repository_checks = next(
            source for source in intelligence["sources"] if source["id"] == "repository-checks"
        )
        self.assertEqual(repository_checks["status"], "connected")
        self.assertEqual(repository_checks["records"], 1)
        self.assertEqual(repository_checks["stores"], "content-free outcomes from approved validation events")
        graph = task_graph(self.project, intelligence=intelligence)
        recommendation_id = "recommendation:next-opportunity-review"
        self.assertTrue(any(edge["target"] == recommendation_id and edge["type"] == "motivates"
                            for edge in graph["edges"]))

    def test_missing_validation_recommendation_has_provenance(self) -> None:
        with connect(self.project) as connection:
            connection.execute("UPDATE tasks SET status='completed',completed_at=updated_at")
        intelligence = project_intelligence(self.project)
        self.assertEqual(intelligence["recommendation"]["id"], "next-evidence-coverage")
        self.assertEqual(intelligence["recommendation"]["signalIds"], ["coverage:validation"])
        graph = task_graph(self.project, intelligence=intelligence)
        self.assertTrue(any(edge["source"] == "signal:coverage:validation" and
                            edge["target"] == "recommendation:next-evidence-coverage"
                            for edge in graph["edges"]))

    def test_period_insights_are_clickable_and_bounded(self) -> None:
        for period, maximum in (("day", 6), ("week", 7), ("month", 5), ("year", 12)):
            data = json.loads(self.get(f"/api/insights?period={period}&token={self.token}").read())
            self.assertEqual(data["period"], period)
            self.assertLessEqual(len(data["buckets"]), maximum)
            self.assertIn("eventIds", data["buckets"][0])
            self.assertIn("totalEvents", data["buckets"][0])
            self.assertIn("other", data["totals"])
            self.assertIn("completedTasks", data["totals"])
            self.assertIn("comparison", data)
            self.assertIn("progressGuidance", data)
            self.assertIn("nextAction", data["progressGuidance"])
            self.assertIn("totals", data["comparison"])
            self.assertTrue(all("occurredLocal" in item for item in data["activity"]))
            self.assertIn("completed", data["totals"])
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.get(f"/api/insights?period=decade&token={self.token}")
        self.assertEqual(failure.exception.code, 400)
        failure.exception.close()

    def test_period_totals_remain_exact_when_detail_is_bounded(self) -> None:
        occurred_at = datetime.now(UTC).isoformat()
        with connect(self.project) as connection:
            project_hash = connection.execute("SELECT project_hash FROM projects LIMIT 1").fetchone()[0]
            connection.executemany(
                "INSERT INTO task_events(id,project_hash,task_id,event_type,summary,occurred_at,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                [(f"bulk-{index}", project_hash, "task-ui", "task_continued", "Bulk evidence",
                  occurred_at, occurred_at) for index in range(501)],
            )
        data = json.loads(self.get(f"/api/insights?period=week&token={self.token}").read())
        self.assertTrue(data["bounds"]["activityTruncated"])
        self.assertEqual(data["bounds"]["activityReturned"], 500)
        self.assertEqual(data["totals"]["totalEvents"], data["bounds"]["activityTotal"])
        self.assertEqual(data["totals"]["other"], 501)

    def test_period_end_is_exclusive(self) -> None:
        self.post("/api/settings", {"timeZone": "UTC"}).read()
        boundary = "2026-03-15T00:00:00+00:00"
        with connect(self.project) as connection:
            project_hash = connection.execute("SELECT project_hash FROM projects LIMIT 1").fetchone()[0]
            connection.execute(
                "INSERT INTO task_events(id,project_hash,task_id,event_type,summary,occurred_at,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("boundary-event", project_hash, "task-ui", "task_continued", "Boundary",
                 boundary, boundary),
            )
        prior = json.loads(self.get(
            f"/api/insights?period=day&anchor=2026-03-14&token={self.token}"
        ).read())
        following = json.loads(self.get(
            f"/api/insights?period=day&anchor=2026-03-15&token={self.token}"
        ).read())
        self.assertEqual(prior["totals"]["totalEvents"], 0)
        self.assertEqual(following["totals"]["totalEvents"], 1)
        tokyo = json.loads(self.get(
            f"/api/insights?period=day&anchor=2026-03-15&timeZone=Asia%2FTokyo&token={self.token}"
        ).read())
        self.assertEqual(tokyo["timeZone"], "Asia/Tokyo")
        historical = json.loads(self.get(
            f"/api/insights?period=month&anchor=2026-03-14&token={self.token}"
        ).read())
        self.assertEqual(historical["anchor"], "2026-03-14")
        self.assertTrue(historical["periodStart"].startswith("2026-03-01"))
        self.assertIsInstance(historical["canMoveNext"], bool)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.get(f"/api/insights?period=week&anchor=not-a-date&token={self.token}")
        self.assertEqual(failure.exception.code, 400)
        failure.exception.close()

    def test_temporal_map_and_calendar_are_backend_scoped(self) -> None:
        data = json.loads(self.get(
            f"/api/map?period=week&timeZone=UTC&token={self.token}"
        ).read())
        graph = data["graph"]
        self.assertEqual(graph["contractVersion"], 2)
        self.assertEqual(graph["period"], "week")
        self.assertTrue(all(edge["source"] in {node["id"] for node in graph["nodes"]}
                            and edge["target"] in {node["id"] for node in graph["nodes"]}
                            for edge in graph["edges"]))
        self.assertIn("not productivity", data["calendar"]["caution"])
        self.assertEqual(len(data["calendar"]["days"]), 365)
        self.assertTrue(any(item["evidence"] >= 1 for item in data["calendar"]["days"]))
        history = json.loads(self.get(
            f"/api/map?period=all&timeZone=UTC&token={self.token}"
        ).read())["graph"]
        self.assertEqual(history["period"], "all")
        self.assertIn("bounded", history["guidance"]["caution"])

    def test_ui_degrades_when_temporal_map_endpoint_is_from_newer_ui(self) -> None:
        script = self.get(f"/insights.js?token={self.token}").read().decode()
        self.assertIn(".catch(error => ({compatibilityError: error.message}))", script)
        self.assertIn("This dashboard server is older than the Map UI", script)
        self.assertIn("if (mapCompatibilityError) state.graph = null", script)

    def test_map_ui_exposes_dates_density_and_trace_action(self) -> None:
        script = self.get(f"/insights.js?token={self.token}").read().decode()
        self.assertIn("data-trace-map-evidence", script)
        self.assertIn("calendar-legend", script)
        self.assertIn("aria-pressed", script)
        self.assertIn("toLocaleDateString", script)

    def test_settings_ui_uses_constrained_choices(self) -> None:
        actions = self.get(f"/actions.js?token={self.token}").read().decode()
        self.assertIn("settingSelect('language'", actions)
        self.assertIn("settingSelect('timeZone'", actions)
        self.assertIn("settingSelect('retentionDays'", actions)
        self.assertIn("Previously saved:", actions)
        self.assertIn("data-notification-action", actions)
        self.assertIn("data-notification-state", actions)
        self.assertIn("data-notification-filter", actions)
        self.assertIn("Mark ${item.state", actions)
        index = self.get(f"/?token={self.token}").read().decode()
        self.assertIn('id="notificationButton"', index)
        self.assertIn('id="view-notifications"', index)
        self.assertIn('id="view-settings"', index)
        self.assertNotIn("Local settings</h2>", index)

    def test_notification_setup_couples_in_app_and_computer_delivery(self) -> None:
        with mock.patch("coderai.project_tasks.server.install_scheduler", return_value={
            "installed": True, "platform": "Darwin", "intervalMode": "daily-at-18:00-local",
        }) as install:
            installed = json.loads(self.post("/api/notifications/scheduler", {"action": "install"}).read())
        self.assertTrue(installed["installed"])
        install.assert_called_once_with(self.project, 1800)
        self.assertTrue(json.loads(self.get(f"/api/settings?token={self.token}").read())["notificationsEnabled"])
        with mock.patch("coderai.project_tasks.server.uninstall_scheduler", return_value={
            "installed": False, "platform": "Darwin", "intervalMode": "daily-at-18:00-local",
        }) as uninstall:
            removed = json.loads(self.post("/api/notifications/scheduler", {"action": "uninstall"}).read())
        self.assertFalse(removed["installed"])
        uninstall.assert_called_once_with(self.project)
        self.assertFalse(json.loads(self.get(f"/api/settings?token={self.token}").read())["notificationsEnabled"])

    def test_rejects_dns_rebinding_host(self) -> None:
        request = urllib.request.Request(f"{self.base}/api/overview?token={self.token}", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(failure.exception.code, 403)
        failure.exception.close()

    def test_action_endpoints_update_settings_ideas_and_task(self) -> None:
        settings = json.loads(self.post("/api/settings", {
            "retentionDays": 30, "language": "Japanese", "gitMetadata": False,
            "notificationsEnabled": True,
        }).read())
        self.assertEqual(settings["retentionDays"], 30)
        self.assertTrue(settings["automaticCollection"])
        self.assertTrue(settings["notificationsEnabled"])
        notifications = json.loads(self.get(f"/api/notifications?token={self.token}").read())
        self.assertIn("scheduler", notifications)
        self.assertIn("items", notifications)
        self.assertIn("unreadCount", notifications)
        self.assertIn("counts", notifications)
        if notifications["items"]:
            changed = json.loads(self.post("/api/notifications/state", {
                "notificationId": notifications["items"][0]["id"], "state": "read",
            }).read())
            self.assertEqual(changed["state"], "read")
        overview = json.loads(self.get(f"/api/overview?token={self.token}").read())
        idea = overview["ideas"][0]
        updated = json.loads(self.post("/api/ideas/status", {
            "ideaId": idea["id"], "status": "planned",
        }).read())
        self.assertEqual(updated["status"], "planned")
        self.post("/api/corrections", {
            "taskId": "task-ui", "field": "theme", "value": "Dashboard", "reason": "User correction",
        }).read()
        detail = json.loads(self.get(f"/api/tasks/task-ui?token={self.token}").read())
        self.assertEqual(detail["theme"], "Dashboard")
        self.assertIn("evidenceBounds", detail)
        self.assertIn("links", detail["evidenceBounds"])
        self.assertIn("corrections", detail["evidenceBounds"])
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.post("/api/corrections", {
                "taskId": "task-ui", "field": "status", "value": "completed",
            })
        self.assertEqual(failure.exception.code, 400)
        failure.exception.close()
        self.post("/api/corrections", {
            "taskId": "task-ui", "field": "status", "value": "completed", "reason": "Correct state",
        }).read()
        completed = json.loads(self.get(f"/api/tasks/task-ui?token={self.token}").read())
        self.assertIsNotNone(completed["completed_at"])
        self.post("/api/corrections", {
            "taskId": "task-ui", "field": "status", "value": "active", "reason": "Reopened",
        }).read()
        reopened = json.loads(self.get(f"/api/tasks/task-ui?token={self.token}").read())
        self.assertIsNone(reopened["completed_at"])
        exported = json.loads(self.get(f"/api/export?token={self.token}").read())
        self.assertEqual(exported["format"], "project-tasks-export-v2")
        self.assertIn("observations", exported)

    def test_project_coach_is_evidence_bound_and_does_not_store_questions(self) -> None:
        before = json.loads(self.get(f"/api/export?token={self.token}").read())
        response = json.loads(self.post("/api/coach", {
            "question": "Explain effort and api_key=do-not-keep-this",
            "period": "month", "anchor": "2026-03-14",
        }).read())
        self.assertFalse(response["stored"])
        self.assertIn("codex", response["agents"])
        self.assertIn("available", response["agents"]["codex"])
        self.assertTrue(response["redactions"])
        self.assertIn("Missing effort is unknown", response["explanation"])
        self.assertIn("Do not rank people", response["caution"])
        self.assertEqual(response["period"], "month")
        self.assertEqual(response["anchor"], "2026-03-14")
        after = json.loads(self.get(f"/api/export?token={self.token}").read())
        self.assertEqual(before, after)
        self.assertNotIn("do-not-keep-this", json.dumps(response))


if __name__ == "__main__":
    unittest.main()
