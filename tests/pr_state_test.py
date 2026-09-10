"""Task 03 — session store, lock, and audit log: confined, atomic, resumable."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from coderai.pr_automation import audit
from coderai.pr_automation.state import (
    SCHEMA_VERSION, Session, SessionLock, StateError, ensure_dir, list_sessions,
    load_session, new_session, read_json, resume, save_session, session_dir, write_json,
)


class FakePR:
    host, owner, repo, number = "github.com", "org", "aiila", 1420
    title, author, url = "Fix worker retry", "nobin", "https://github.com/org/aiila/pull/1420"
    head_branch, base_branch, remote = "feature/worker-retry", "main", "origin"
    head_sha = "abc123abc123abc123abc123abc123abc123abcd"


def a_session(**kwargs) -> Session:
    return new_session(FakePR(), provider="claude", provider_argv=["claude"],
                       watch_mode="bounded", watch_seconds=3600, **kwargs)


class Paths(unittest.TestCase):
    def test_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = session_dir("org", "aiila", 1420, root=Path(tmp))
            self.assertEqual(path, Path(tmp) / "org" / "aiila" / "1420")

    def test_unsafe_components_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for owner, repo in ((".." , "aiila"), ("org", ".."), ("org/../x", "aiila"),
                                ("", "aiila"), ("org", "a" * 200)):
                with self.subTest(owner=owner, repo=repo), self.assertRaises(StateError):
                    session_dir(owner, repo, 1420, root=Path(tmp))

    def test_symlinked_component_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            (root / "org").symlink_to(elsewhere)
            with self.assertRaises(StateError):
                session_dir("org", "aiila", 1420, root=root)


class AtomicWrites(unittest.TestCase):
    def test_write_then_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "session.json"
            write_json(target, {"a": 1})
            self.assertEqual(read_json(target), {"a": 1})
            self.assertEqual(oct(target.stat().st_mode)[-3:], "600")

    def test_crash_mid_write_leaves_the_previous_value(self) -> None:
        """A killed writer must never leave truncated JSON behind."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "session.json"
            write_json(target, {"generation": 1})
            script = (
                "import os,sys,json,signal\n"
                "sys.path.insert(0, %r)\n"
                "from coderai.pr_automation.state import write_json\n"
                "from pathlib import Path\n"
                "import coderai.pr_automation.state as state\n"
                "real = os.replace\n"
                "def boom(a, b):\n"
                "    os.kill(os.getpid(), signal.SIGKILL)\n"
                "os.replace = boom\n"
                "write_json(Path(%r), {'generation': 2})\n"
            ) % (str(Path(__file__).resolve().parents[1] / "src"), str(target))
            subprocess.run([sys.executable, "-c", script], capture_output=True)
            # The rename never happened, so the previous value is intact and complete.
            self.assertEqual(read_json(target), {"generation": 1})
            # SIGKILL cannot run cleanup, so a temp file may remain — the next write
            # sweeps it once it is stale, rather than letting them accumulate forever.
            stale = list(Path(tmp).glob(".session.json.*.tmp"))
            self.assertEqual(len(stale), 1)
            os.utime(stale[0], (0, 0))
            write_json(target, {"generation": 3})
            self.assertEqual(list(Path(tmp).glob(".session.json.*.tmp")), [])
            self.assertEqual(read_json(target), {"generation": 3})

    def test_non_regular_file_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "session.json"
            target.symlink_to(Path(tmp) / "elsewhere.json")
            (Path(tmp) / "elsewhere.json").write_text("{}")
            with self.assertRaises(StateError):
                read_json(target)


class SessionRoundTrip(unittest.TestCase):
    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = a_session()
            save_session(session, root=root)
            loaded = load_session("org", "aiila", 1420, root=root)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.head_branch, "feature/worker-retry")
            self.assertEqual(loaded.provider, "claude")
            self.assertEqual(loaded.schema_version, SCHEMA_VERSION)

    def test_directory_and_file_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = save_session(a_session(), root=root)
            self.assertEqual(oct(directory.stat().st_mode)[-3:], "700")
            self.assertEqual(oct((directory / "session.json").stat().st_mode)[-3:], "600")

    def test_watch_deadline_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = a_session(now=1000.0)
            self.assertEqual(session.watch_deadline, 4600.0)
            save_session(session, root=root)
            loaded = load_session("org", "aiila", 1420, root=root)
            assert loaded is not None
            self.assertEqual(loaded.watch_deadline, 4600.0)

    def test_copied_state_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_session(a_session(), root=root)
            source = session_dir("org", "aiila", 1420, root=root)
            target = ensure_dir(session_dir("other", "thing", 7, root=root), root)
            (target / "session.json").write_text((source / "session.json").read_text())
            with self.assertRaises(StateError) as caught:
                load_session("other", "thing", 7, root=root)
            self.assertIn("copied state", str(caught.exception))

    def test_future_schema_version_refuses_rather_than_guesses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = save_session(a_session(), root=root)
            value = json.loads((directory / "session.json").read_text())
            value["schema_version"] = SCHEMA_VERSION + 5
            (directory / "session.json").write_text(json.dumps(value))
            with self.assertRaises(StateError) as caught:
                load_session("org", "aiila", 1420, root=root)
            self.assertIn("newer coder-ai-os", str(caught.exception))

    def test_unknown_keys_within_a_version_are_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = save_session(a_session(), root=root)
            value = json.loads((directory / "session.json").read_text())
            value["a_future_field"] = "hello"
            (directory / "session.json").write_text(json.dumps(value))
            loaded = load_session("org", "aiila", 1420, root=root)
            self.assertIsNotNone(loaded)

    def test_resume_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(resume("org", "aiila", 1420, root=root)[1], "NO_PRIOR_SESSION")
            session = a_session()
            save_session(session, root=root)
            self.assertEqual(resume("org", "aiila", 1420, root=root)[1], "RESUMED")
            session.state = "MERGED"
            save_session(session, root=root)
            self.assertTrue(resume("org", "aiila", 1420, root=root)[1]
                            .startswith("PRIOR_SESSION_TERMINAL"))

    def test_list_sessions_skips_corrupt_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_session(a_session(), root=root)
            broken = ensure_dir(session_dir("org", "aiila", 9, root=root), root)
            (broken / "session.json").write_text("{ not json")
            self.assertEqual([item.number for item in list_sessions(root)], [1420])


class Locking(unittest.TestCase):
    def test_second_supervisor_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = ensure_dir(session_dir("org", "aiila", 1420, root=Path(tmp)), Path(tmp))
            with SessionLock(directory, root=Path(tmp)):
                with self.assertRaises(StateError) as caught:
                    SessionLock(directory, root=Path(tmp)).acquire()
                self.assertIn("already supervised", str(caught.exception))

    def test_lock_released_on_normal_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = ensure_dir(session_dir("org", "aiila", 1420, root=Path(tmp)), Path(tmp))
            with SessionLock(directory, root=Path(tmp)) as lock:
                self.assertTrue(lock.path.exists())
            self.assertFalse(lock.path.exists())
            SessionLock(directory, root=Path(tmp)).acquire().release()

    def test_stale_lock_from_a_dead_pid_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = ensure_dir(session_dir("org", "aiila", 1420, root=Path(tmp)), Path(tmp))
            lock_path = directory / "session.lock"
            write_json(lock_path, {"pid": 999_999, "started": "", "at": time.time(),
                                   "host": os.uname().nodename})
            SessionLock(directory, root=Path(tmp)).acquire().release()

    def test_recycled_pid_cannot_impersonate_the_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = ensure_dir(session_dir("org", "aiila", 1420, root=Path(tmp)), Path(tmp))
            write_json(directory / "session.lock",
                       {"pid": os.getpid(), "started": "Wed Jan  1 00:00:00 1990",
                        "at": time.time(), "host": os.uname().nodename})
            SessionLock(directory, root=Path(tmp)).acquire().release()

    def test_corrupt_lock_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = ensure_dir(session_dir("org", "aiila", 1420, root=Path(tmp)), Path(tmp))
            (directory / "session.lock").write_text("garbage")
            SessionLock(directory, root=Path(tmp)).acquire().release()

    def test_sigterm_releases_the_lock(self) -> None:
        script = (
            "import os, sys, signal, time\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r})\n"
            "from pathlib import Path\n"
            "from coderai.pr_automation.state import SessionLock, ensure_dir, session_dir\n"
            "root = Path(sys.argv[1])\n"
            "d = ensure_dir(session_dir('org','aiila',1420, root=root), root)\n"
            "with SessionLock(d, root=root):\n"
            "    print('locked', flush=True)\n"
            "    time.sleep(30)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            child = subprocess.Popen([sys.executable, "-c", script, tmp],
                                     stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "locked")
                child.terminate()
                child.wait(timeout=15)
            finally:
                if child.poll() is None:
                    child.kill()
            directory = session_dir("org", "aiila", 1420, root=Path(tmp))
            self.assertFalse((directory / "session.lock").exists())


def _append_many(args) -> None:
    directory, count = args
    for index in range(count):
        audit.append(Path(directory), "tick", index=index)


class Audit(unittest.TestCase):
    def test_append_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            audit.append(directory, "ai_started", role="fix", provider="codex")
            audit.append(directory, "push_complete", commit="91ad773")
            records = audit.read(directory)
            self.assertEqual([item["event"] for item in records],
                             ["ai_started", "push_complete"])
            self.assertEqual([item["seq"] for item in records], [1, 2])
            self.assertEqual(records[0]["provider"], "codex")

    def test_concurrent_appends_stay_valid_line_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with multiprocessing.Pool(4) as pool:
                pool.map(_append_many, [(tmp, 25)] * 4)
            lines = (Path(tmp) / "audit.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 100)
            for line in lines:
                json.loads(line)
            sequences = sorted(json.loads(line)["seq"] for line in lines)
            self.assertEqual(sequences, list(range(1, 101)))

    def test_fields_are_clipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record = audit.append(Path(tmp), "big", blob="x" * 100_000)
            self.assertLessEqual(len(record["blob"]), 4000)
            line = (Path(tmp) / "audit.jsonl").read_text()
            self.assertLessEqual(len(line), 32_001)

    def test_mode_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit.append(Path(tmp), "x")
            self.assertEqual(oct((Path(tmp) / "audit.jsonl").stat().st_mode)[-3:], "600")

    def test_tail_after_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(5):
                audit.append(Path(tmp), "tick", index=index)
            self.assertEqual([item["seq"] for item in audit.tail(Path(tmp), after_seq=3)], [4, 5])


if __name__ == "__main__":
    unittest.main(verbosity=2)
