"""Task 17 — background mode, and no supervisor outliving its session record."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from coderai.pr_automation import audit, commands, daemon
from coderai.pr_automation.state import (
    Session, ensure_dir, save_session, session_dir,
)

REPO = Path(__file__).resolve().parents[1]


def a_session(number: int = 1420, state: str = "WAITING_FOR_REVIEW") -> Session:
    session = Session(host="github.com", owner="org", repo="aiila", number=number,
                      head_branch="feature/worker-retry", base_branch="main",
                      remote="origin", provider="claude", provider_argv=["claude"],
                      title="Fix worker retry")
    session.state = state
    session.head_sha = "91ad7734ee0000000000000000000000000000aa"
    session.updated_at = time.time()
    return session


class StopChannel(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.directory = ensure_dir(session_dir("org", "aiila", 1420, root=self.root),
                                    self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_request_and_clear(self) -> None:
        self.assertFalse(daemon.stop_requested(self.directory))
        daemon.request_stop(self.directory)
        self.assertTrue(daemon.stop_requested(self.directory))
        self.assertEqual(oct((self.directory / daemon.STOP_FILE).stat().st_mode)[-3:], "600")
        daemon.clear_stop(self.directory)
        self.assertFalse(daemon.stop_requested(self.directory))

    def test_stopping_when_nothing_runs_still_records_the_request(self) -> None:
        self.assertEqual(daemon.stop(self.directory, grace=0.1), "NOT_RUNNING")
        self.assertTrue(daemon.stop_requested(self.directory))

    def test_a_recycled_pid_cannot_impersonate_the_supervisor(self) -> None:
        from coderai.pr_automation.state import write_json
        write_json(self.directory / daemon.PID_FILE,
                   {"pid": os.getpid(), "started": "Wed Jan  1 00:00:00 1990",
                    "at": time.time()})
        supervisor = daemon.read_pid(self.directory)
        assert supervisor is not None
        self.assertFalse(supervisor.alive())

    def test_a_live_supervisor_is_detected_and_stopped(self) -> None:
        script = (
            "import sys, time\n"
            f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
            "from pathlib import Path\n"
            "from coderai.pr_automation import daemon\n"
            "directory = Path(sys.argv[1])\n"
            "daemon.write_pid(directory)\n"
            "print('up', flush=True)\n"
            "while not daemon.stop_requested(directory):\n"
            "    time.sleep(0.05)\n"
            "daemon.clear_pid(directory)\n"
        )
        child = subprocess.Popen([sys.executable, "-c", script, str(self.directory)],
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "up")
            supervisor = daemon.read_pid(self.directory)
            assert supervisor is not None
            self.assertTrue(supervisor.alive())
            self.assertEqual(daemon.stop(self.directory, grace=10.0), "STOPPED")
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
        self.assertIsNone(daemon.read_pid(self.directory))

    def test_an_ignored_stop_is_escalated_to_sigterm(self) -> None:
        script = (
            "import sys, time, signal\n"
            f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
            "from pathlib import Path\n"
            "from coderai.pr_automation import daemon\n"
            "daemon.write_pid(Path(sys.argv[1]))\n"
            "print('up', flush=True)\n"
            "time.sleep(120)\n"
        )
        child = subprocess.Popen([sys.executable, "-c", script, str(self.directory)],
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "up")
            self.assertEqual(daemon.stop(self.directory, grace=1.0), "TERMINATED")
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()

    def test_spawn_detaches_and_captures_output(self) -> None:
        argv = [sys.executable, "-c", "print('detached child')"]
        pid = daemon.spawn(argv, self.directory, cwd=self.root)
        self.assertGreater(pid, 0)
        log = self.directory / daemon.LOG_FILE
        for _ in range(100):
            if log.is_file() and "detached" in log.read_text():
                break
            time.sleep(0.05)
        self.assertIn("detached child", log.read_text())
        self.assertEqual(oct(log.stat().st_mode)[-3:], "600")


class Commands(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.lines: list[str] = []
        self.out = lambda text: self.lines.append(str(text))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def seed(self, session: Session) -> Path:
        directory = ensure_dir(session_dir(session.owner, session.repo, session.number,
                                           root=self.root), self.root)
        save_session(session, root=self.root)
        return directory

    def text(self) -> str:
        return "\n".join(self.lines)

    def test_status_lists_every_session(self) -> None:
        self.seed(a_session(1420))
        self.seed(a_session(1421, state="WAITING_FOR_CI"))
        self.seed(a_session(1430, state="READY"))
        self.assertEqual(commands.status(None, root=self.root, out=self.out), 0)
        text = self.text()
        self.assertIn("#1420", text)
        self.assertIn("WAITING_FOR_CI", text)
        self.assertIn("READY", text)

    def test_status_for_one_pr_shows_the_waiting_pane(self) -> None:
        directory = self.seed(a_session(1420))
        audit.append(directory, "ai_finished", provider="claude",
                     summary="fixed and pushed retry handling")
        self.assertEqual(commands.status("1420", root=self.root, out=self.out), 0)
        text = self.text()
        self.assertIn("coder-ai · PR #1420", text)
        self.assertIn("AI         sleeping", text)
        self.assertIn("fixed and pushed retry handling", text)
        self.assertIn("resume with", text)

    def test_status_matches_by_branch_or_slug(self) -> None:
        self.seed(a_session(1420))
        self.assertEqual(commands.status("feature/worker-retry", root=self.root,
                                         out=self.out), 0)
        self.assertIn("#1420", self.text())
        self.lines.clear()
        self.assertEqual(commands.status("org/aiila#1420", root=self.root, out=self.out), 0)
        self.assertIn("#1420", self.text())

    def test_unknown_target_is_reported(self) -> None:
        self.assertEqual(commands.status("9999", root=self.root, out=self.out), 1)
        self.assertIn("no session", self.text())

    def test_log_renders_the_audit_trail(self) -> None:
        directory = self.seed(a_session(1420))
        audit.append(directory, "ai_started", role="fix", provider="codex")
        audit.append(directory, "push_complete", commit="91ad773")
        self.assertEqual(commands.log("1420", root=self.root, out=self.out), 0)
        text = self.text()
        self.assertIn("ai_started", text)
        self.assertIn("push_complete", text)
        self.assertIn("91ad773", text)

    def test_stop_records_the_request_and_reports(self) -> None:
        directory = self.seed(a_session(1420))
        self.assertEqual(commands.stop("1420", root=self.root, out=self.out,
                                       sleep=lambda _s: None), 0)
        self.assertTrue(daemon.stop_requested(directory))
        self.assertIn("no supervisor was running", self.text())
        events = [item["event"] for item in audit.read(directory)]
        self.assertIn("user_stop", events)

    def test_attach_returns_on_a_terminal_session(self) -> None:
        session = a_session(1420, state="MERGED")
        self.seed(session)
        self.assertEqual(commands.attach("1420", root=self.root, out=self.out,
                                         sleep=lambda _s: None), 0)
        self.assertIn("MERGED", self.text())

    def test_attach_streams_events_then_detaches_without_stopping(self) -> None:
        directory = self.seed(a_session(1420))
        audit.append(directory, "ai_wake", reason="NEW_DISCUSSION")
        audit.append(directory, "push_complete", commit="91ad773")
        self.assertEqual(commands.attach("1420", root=self.root, out=self.out,
                                         sleep=lambda _s: None, iterations=1), 0)
        text = self.text()
        self.assertIn("ai_wake", text)
        self.assertIn("push_complete", text)
        self.assertFalse(daemon.stop_requested(directory),
                         "detaching must never stop the session")

    def test_commands_work_on_finished_sessions(self) -> None:
        session = a_session(1420, state="WATCH_TIMEOUT")
        directory = self.seed(session)
        audit.append(directory, "session_finished", reason="WATCH_TIMEOUT")
        self.assertEqual(commands.status("1420", root=self.root, out=self.out), 0)
        self.assertIn("WATCH_TIMEOUT", self.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
