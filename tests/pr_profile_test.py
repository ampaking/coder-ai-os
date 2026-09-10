"""Task 06 — the elevated profile is session-scoped and cannot be widened."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import pr_repo  # noqa: E402

from coderai.pr_automation.guard import install as guard_install  # noqa: E402
from coderai.pr_automation.notice import permission_notice, watch_expired_report  # noqa: E402
from coderai.pr_automation.profile import (  # noqa: E402
    BASE_PERMISSIONS, ProfileError, build_claude_settings, build_profile,
    sanitize_provider_argv,
)
from coderai.pr_automation.state import Session  # noqa: E402
from coderai.pr_automation.worktree import ensure_worktree  # noqa: E402

HEAD_BRANCH = "feature/worker-retry"


def a_session(provider: str = "claude") -> Session:
    return Session(host="github.com", owner="org", repo="aiila", number=1420,
                   head_branch=HEAD_BRANCH, base_branch="main", remote="origin",
                   provider=provider, provider_argv=[provider], title="Fix worker retry")


class Environment(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.sessions = self.root / "session"
        self.sessions.mkdir()
        self.session = a_session()
        self.tree = ensure_worktree(self.session, self.clone, root=self.root / "worktrees")
        self.guard = guard_install.install(
            self.sessions, self.tree.path, remote="origin", head_branch=HEAD_BRANCH,
            local_branch=self.tree.branch)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class ClaudeSettings(Environment):
    def test_grants_git_writes(self) -> None:
        settings = build_claude_settings(self.guard)
        allow = settings["permissions"]["allow"]
        deny = settings["permissions"]["deny"]
        for rule in ("Bash(git add:*)", "Bash(git commit:*)", "Bash(git push:*)"):
            self.assertIn(rule, allow)
            self.assertNotIn(rule, deny)

    def test_keeps_every_secret_denial(self) -> None:
        base = json.loads(BASE_PERMISSIONS.read_text())["permissions"]["deny"]
        secrets = [rule for rule in base if "env" in rule.lower() or "secret" in rule.lower()
                   or "service-account" in rule]
        self.assertTrue(secrets, "the base profile should deny secrets")
        deny = build_claude_settings(self.guard)["permissions"]["deny"]
        for rule in secrets:
            self.assertIn(rule, deny)

    def test_keeps_destructive_denials_and_adds_pr_specific_ones(self) -> None:
        """Only the irreversible half. A session that cannot rebase cannot finish a PR."""
        deny = build_claude_settings(self.guard)["permissions"]["deny"]
        for rule in ("Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(sudo:*)",
                     "Bash(gh pr merge:*)", "Bash(/usr/bin/git:*)"):
            self.assertIn(rule, deny)
        for rule in ("Bash(git rebase:*)", "Bash(git merge:*)", "Bash(git checkout:*)"):
            self.assertNotIn(rule, deny,
                             f"AUTOMATION STALL: {rule} is blocked inside a PR session")

    def test_settings_file_is_private_and_session_scoped(self) -> None:
        plan = build_profile("claude", ["claude"], guard=self.guard, worktree=self.tree.path)
        assert plan.settings_path is not None
        self.assertEqual(oct(plan.settings_path.stat().st_mode)[-3:], "600")
        self.assertTrue(str(plan.settings_path).startswith(str(self.sessions)))
        plan.cleanup()
        self.assertFalse(plan.settings_path.exists())


class UserConfigUntouched(Environment):
    def _checksums(self) -> dict[str, str]:
        import hashlib

        targets = [BASE_PERMISSIONS,
                   Path(__file__).resolve().parents[1] / "codex" / "config.defaults.toml",
                   Path(__file__).resolve().parents[1] / "config" / "safety.yaml"]
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in targets if path.is_file()}

    def test_shipped_profiles_are_not_modified_by_building_one(self) -> None:
        before = self._checksums()
        plan = build_profile("claude", ["claude"], guard=self.guard, worktree=self.tree.path)
        plan.cleanup()
        self.assertEqual(self._checksums(), before)

    def test_nothing_is_written_into_the_home_config(self) -> None:
        home_settings = Path.home() / ".claude" / "settings.json"
        before = home_settings.read_bytes() if home_settings.is_file() else None
        plan = build_profile("claude", ["claude"], guard=self.guard, worktree=self.tree.path)
        plan.cleanup()
        after = home_settings.read_bytes() if home_settings.is_file() else None
        self.assertEqual(after, before)


class LaunchPlans(Environment):
    def test_claude_launch(self) -> None:
        plan = build_profile("claude", ["claude", "--model", "claude-fable-5"],
                             guard=self.guard, worktree=self.tree.path)
        self.assertEqual(plan.argv[0], "claude")
        self.assertIn("--settings", plan.argv)
        self.assertEqual(plan.argv[-2:], ["--model", "claude-fable-5"])
        self.assertEqual(plan.cwd, self.tree.path)
        plan.cleanup()

    def test_codex_launch_is_pinned_to_the_worktree(self) -> None:
        plan = build_profile("codex", ["codex", "--model", "gpt-5.6-sol"],
                             guard=self.guard, worktree=self.tree.path)
        self.assertEqual(plan.argv[:7], ["codex", "--cd", str(self.tree.path), "--sandbox",
                                         "workspace-write", "--ask-for-approval", "never"])
        self.assertEqual(plan.argv[-2:], ["--model", "gpt-5.6-sol"])
        plan.cleanup()

    def test_guard_precedes_system_git_in_the_child_path(self) -> None:
        plan = build_profile("claude", ["claude"], guard=self.guard, worktree=self.tree.path)
        first = plan.env["PATH"].split(os.pathsep)[0]
        self.assertEqual(first, str(self.guard.bin_dir))
        result = subprocess.run(["sh", "-c", "command -v git"], env=plan.env,
                                capture_output=True, text=True, cwd=str(plan.cwd))
        self.assertEqual(result.stdout.strip(), str(self.guard.bin_dir / "git"))
        plan.cleanup()

    def test_relocating_git_environment_is_stripped(self) -> None:
        os.environ["GIT_DIR"] = "/somewhere/else/.git"
        try:
            plan = build_profile("claude", ["claude"], guard=self.guard,
                                 worktree=self.tree.path)
            self.assertNotIn("GIT_DIR", plan.env)
            plan.cleanup()
        finally:
            os.environ.pop("GIT_DIR", None)

    def test_session_context_is_exported(self) -> None:
        plan = build_profile("claude", ["claude"], guard=self.guard,
                             worktree=self.tree.path, session_slug="org/aiila#1420")
        self.assertEqual(plan.env["CODER_AI_PR_MODE"], "1")
        self.assertEqual(plan.env["CODER_AI_PR_BRANCH"], HEAD_BRANCH)
        self.assertEqual(plan.env["CODER_AI_PR_SLUG"], "org/aiila#1420")
        plan.cleanup()


class BoundaryWeakeningArguments(unittest.TestCase):
    def test_claude_arguments_that_dismantle_the_boundary(self) -> None:
        for argv in (["claude", "--dangerously-skip-permissions"],
                     ["claude", "--settings", "/tmp/mine.json"],
                     ["claude", "--permission-mode", "bypassPermissions"],
                     ["claude", "--allowed-tools", "Bash"],
                     ["claude", "--add-dir", "/"]):
            with self.subTest(argv=argv), self.assertRaises(ProfileError):
                sanitize_provider_argv("claude", argv)

    def test_codex_arguments_that_dismantle_the_boundary(self) -> None:
        for argv in (["codex", "--dangerously-bypass-approvals-and-sandbox"],
                     ["codex", "--sandbox", "danger-full-access"],
                     ["codex", "--cd", "/"], ["codex", "--full-auto"],
                     ["codex", "-C", "/etc"]):
            with self.subTest(argv=argv), self.assertRaises(ProfileError):
                sanitize_provider_argv("codex", argv)

    def test_ordinary_arguments_pass_through(self) -> None:
        self.assertEqual(sanitize_provider_argv("claude", ["claude", "--model", "x"]),
                         ["claude", "--model", "x"])
        self.assertEqual(sanitize_provider_argv("codex", ["codex", "--model", "y"]),
                         ["codex", "--model", "y"])


class RepositoryCannotEscalate(Environment):
    def test_worktree_settings_granting_force_push_do_not_take_effect(self) -> None:
        """A PR may edit .claude/settings.json; the guard still refuses (§25)."""
        claude_dir = self.tree.path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({
            "permissions": {"allow": ["Bash(git push --force:*)", "Bash(git merge:*)"],
                            "deny": []}}))
        plan = build_profile("claude", ["claude"], guard=self.guard, worktree=self.tree.path)
        result = subprocess.run(["git", "push", "--force"], env=plan.env,
                                cwd=str(self.tree.path), capture_output=True, text=True)
        self.assertEqual(result.returncode, 13)
        self.assertIn("coder-ai guard: refused", result.stderr)
        plan.cleanup()

    def test_a_repository_makefile_cannot_grant_permission(self) -> None:
        (self.tree.path / "Makefile").write_text("push:\n\tgit push --force\n")
        plan = build_profile("claude", ["claude"], guard=self.guard, worktree=self.tree.path)
        result = subprocess.run(["make", "push"], env=plan.env, cwd=str(self.tree.path),
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        plan.cleanup()


class Notice(unittest.TestCase):
    def test_permission_notice_states_scope(self) -> None:
        text = permission_notice(a_session(), "/tmp/coder-ai/pr-1420")
        self.assertIn("coder-ai · PR Automation", text)
        self.assertIn("#1420", text)
        self.assertIn(f"non-force push {HEAD_BRANCH}", text)
        for blocked in ("force push", "merge", "deploy", "secrets", "branch protection"):
            self.assertIn(blocked, text)

    def test_cross_repository_notice_does_not_promise_pushes(self) -> None:
        text = permission_notice(a_session(), "/tmp/x", allow_push=False)
        self.assertNotIn("✓ non-force push", text)
        self.assertIn("another repository", text)

    def test_watch_expiry_report(self) -> None:
        session = a_session()
        session.state = "OPEN"
        session.head_sha = "91ad7734ee"
        text = watch_expired_report(session, ci="passed", open_findings=0)
        self.assertIn("Watch window ended.", text)
        self.assertIn("91ad773", text)
        self.assertIn("coder-ai pr 1420 --watch 2h -- claude", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
