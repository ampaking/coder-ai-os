"""Task 07 — installing or running PR automation never elevates normal sessions.

This is a release gate. It fails loudly if a future change "makes PR mode work"
by loosening the shipped guardrails, or lets elevation leak past a session.
"""

from __future__ import annotations

import hashlib
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
from coderai.pr_automation.profile import GIT_WRITE_ALLOW, build_profile  # noqa: E402
from coderai.pr_automation.state import Session  # noqa: E402
from coderai.pr_automation.worktree import ensure_worktree  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HEAD_BRANCH = "feature/worker-retry"

# Ordinary git is permitted to a normal session; the irreversible half is not.
# Removing any of these is the exact regression this file exists to catch.
REQUIRED_DENY = (
    "Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(sudo:*)",
    "Read(.env)", "Read(**/secrets/**)",
)


class ShippedGuardrailsIntact(unittest.TestCase):
    def test_normal_claude_profile_still_denies_git_writes(self) -> None:
        value = json.loads((REPO / "claude" / "permissions.json").read_text())
        deny = value["permissions"]["deny"]
        for rule in REQUIRED_DENY:
            self.assertIn(rule, deny,
                          f"NORMAL PROFILE REGRESSION: {rule} is no longer denied")

    def test_pr_mode_allow_rules_are_not_in_the_shipped_profile(self) -> None:
        text = (REPO / "claude" / "permissions.json").read_text()
        value = json.loads(text)["permissions"]
        for rule in GIT_WRITE_ALLOW:
            if rule.startswith("Bash(git add"):
                continue  # staging is not a history write and was never denied
            self.assertNotIn(rule, value["allow"],
                             f"ESCALATION: {rule} leaked into the normal profile")

    def test_an_agent_can_start_a_pr_session(self) -> None:
        """Full automation: a session may launch one. The boundary is what a PR session
        can DO — the guard shim, the worktree, and PR_MODE_DENY — not who starts it."""
        allow = json.loads((REPO / "claude" / "permissions.json").read_text())["permissions"]["allow"]
        for rule in ("Bash(coder-ai pr:*)", "Bash(coder-ai-os pr:*)"):
            self.assertIn(rule, allow, f"AUTOMATION STALL: {rule} cannot be launched")

    def test_the_visual_loop_is_reachable_without_a_prompt(self) -> None:
        """It was installed, hooked, and blocked — so UI changes shipped unverified."""
        allow = json.loads((REPO / "claude" / "permissions.json").read_text())["permissions"]["allow"]
        self.assertIn("Bash(.coder-ai/val/run:*)", allow)
        self.assertIn("Bash(coder-ai val:*)", allow)

    def test_safety_kernel_still_forbids_git_writes(self) -> None:
        """The wording may change; the rule may not."""
        text = (REPO / "config" / "safety.yaml").read_text()
        self.assertIn("forbid_git_write: false", text)
        flat = " ".join(text.split()).lower()
        for rule in ("push to the base branch or any protected branch",
                     "force push", "merge a pr", "deploy", "sudo"):
            with self.subTest(rule=rule):
                self.assertIn(rule, flat, f"the guardrail no longer forbids: {rule}")

    def test_the_guardrail_names_the_one_sanctioned_path(self) -> None:
        flat = " ".join((REPO / "config" / "safety.yaml").read_text().split())
        self.assertIn("coder-ai ship", flat,
                      "an agent told only 'never' has nowhere to go")

    def test_codex_defaults_are_not_elevated(self) -> None:
        path = REPO / "codex" / "config.defaults.toml"
        if not path.is_file():
            self.skipTest("codex defaults not present")
        text = path.read_text()
        self.assertNotIn("danger-full-access", text)
        self.assertNotIn("--dangerously-bypass", text)

    def test_compiled_output_still_carries_the_guardrail_block(self) -> None:
        candidates = list((REPO / "build").rglob("*.md"))
        self.assertTrue(candidates, "no compiled output to check")
        matched = [path for path in candidates
                   if "protected branch" in path.read_text(encoding="utf-8", errors="replace")
                   and "force push" in path.read_text(encoding="utf-8", errors="replace")]
        self.assertTrue(matched,
                        "COMPILED REGRESSION: no compiled file carries the git guardrail")

    def test_pr_automation_config_declares_both_profiles(self) -> None:
        text = (REPO / "config" / "pr_automation.yaml").read_text()
        self.assertIn("normal:", text)
        self.assertIn("git_write: false", text)
        self.assertIn("pr_automation:", text)
        self.assertIn("git_write: scoped", text)


class RuntimeIsolation(unittest.TestCase):
    """A live PR session must not elevate anything outside its own process tree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.sessions = self.root / "session"
        self.sessions.mkdir()
        self.session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                               head_branch=HEAD_BRANCH, base_branch="main", remote="origin",
                               provider="claude", provider_argv=["claude"])
        self.tree = ensure_worktree(self.session, self.clone, root=self.root / "worktrees")
        self.guard = guard_install.install(
            self.sessions, self.tree.path, remote="origin", head_branch=HEAD_BRANCH,
            local_branch=self.tree.branch)
        self.plan = build_profile("claude", ["claude"], guard=self.guard,
                                  worktree=self.tree.path)

    def tearDown(self) -> None:
        self.plan.cleanup()
        self._tmp.cleanup()

    def normal_env(self) -> dict:
        """Exactly what a plain `claude` / `codex` session inherits."""
        return {key: value for key, value in os.environ.items()
                if not key.startswith("CODER_AI_")}

    def test_a_normal_session_running_now_sees_no_guard_shim(self) -> None:
        env = self.normal_env()
        result = subprocess.run(["sh", "-c", "command -v git"], env=env,
                                capture_output=True, text=True, cwd=str(self.clone))
        self.assertNotIn(str(self.guard.bin_dir), result.stdout)

    def test_a_normal_session_has_no_pr_mode_markers(self) -> None:
        env = self.normal_env()
        for name in ("CODER_AI_PR_MODE", "CODER_AI_GUARD_CONFIG", "CODER_AI_REAL_GIT",
                     "CODER_AI_PR_WORKTREE"):
            self.assertNotIn(name, env)

    def test_home_and_project_settings_are_never_written(self) -> None:
        targets = [Path.home() / ".claude" / "settings.json",
                   REPO / ".claude" / "settings.json",
                   Path.home() / ".codex" / "config.toml"]
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in targets if path.is_file()}
        plan = build_profile("claude", ["claude"], guard=self.guard,
                             worktree=self.tree.path)
        subprocess.run(["git", "status"], env=plan.env, cwd=str(self.tree.path),
                       capture_output=True)
        plan.cleanup()
        after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in targets if path.is_file()}
        self.assertEqual(after, before, "PR mode wrote into user or project configuration")

    def test_the_elevated_settings_file_lives_only_in_the_session(self) -> None:
        assert self.plan.settings_path is not None
        self.assertTrue(str(self.plan.settings_path).startswith(str(self.sessions)))

    def test_a_leftover_shim_does_not_elevate_a_later_session(self) -> None:
        """Even if the shim directory survives a crash, it grants nothing alone."""
        env = self.normal_env()
        env["PATH"] = os.pathsep.join([str(self.guard.bin_dir), env.get("PATH", "")])
        result = subprocess.run(["git", "status"], env=env, cwd=str(self.tree.path),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 13)
        self.assertIn("no PR-automation scope", result.stderr)

    def test_terminal_states_remove_every_elevated_artifact(self) -> None:
        for state in ("MERGED", "CLOSED", "WATCH_TIMEOUT", "USER_STOP"):
            with self.subTest(state=state):
                plan = build_profile("claude", ["claude"], guard=self.guard,
                                     worktree=self.tree.path)
                guard = guard_install.install(
                    self.sessions, self.tree.path, remote="origin",
                    head_branch=HEAD_BRANCH, local_branch=self.tree.branch)
                plan.cleanup()
                guard_install.uninstall(guard, self.tree.path)
                self.assertFalse(guard.bin_dir.exists())
                self.assertFalse(guard.config_path.exists())
                assert guard.hook_path is not None
                self.assertFalse(guard.hook_path.exists())
                assert plan.settings_path is not None
                self.assertFalse(plan.settings_path.exists())

    def test_killed_session_leaves_nothing_that_grants_permission(self) -> None:
        """SIGKILL cannot run cleanup — prove the residue is inert."""
        script = (
            f"import sys, os, time\n"
            f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
            f"from pathlib import Path\n"
            f"from coderai.pr_automation.guard import install as gi\n"
            f"g = gi.install(Path({str(self.sessions)!r}), Path({str(self.tree.path)!r}),"
            f" remote='origin', head_branch={HEAD_BRANCH!r}, local_branch={self.tree.branch!r})\n"
            f"print(g.bin_dir, flush=True)\n"
            f"os.kill(os.getpid(), 9)\n"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        bin_dir = result.stdout.strip()
        self.assertTrue(bin_dir)
        env = self.normal_env()
        env["PATH"] = os.pathsep.join([bin_dir, env.get("PATH", "")])
        check = subprocess.run(["git", "push", "--force"], env=env,
                               cwd=str(self.tree.path), capture_output=True, text=True)
        self.assertNotEqual(check.returncode, 0)


class TwoTerminals(unittest.TestCase):
    """§13: Terminal A (normal) and Terminal B (PR automation), side by side."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin, self.clone = pr_repo.build(self.root)
        self.sessions = self.root / "session"
        self.sessions.mkdir()
        session = Session(host="github.com", owner="org", repo="aiila", number=1420,
                          head_branch=HEAD_BRANCH, base_branch="main", remote="origin",
                          provider="codex", provider_argv=["codex"])
        self.tree = ensure_worktree(session, self.clone, root=self.root / "worktrees")
        self.guard = guard_install.install(
            self.sessions, self.tree.path, remote="origin", head_branch=HEAD_BRANCH,
            local_branch=self.tree.branch)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_terminal_b_may_commit_and_push_the_pr_branch(self) -> None:
        env = {**os.environ, **self.guard.env}
        (self.tree.path / "worker" / "retry.py").write_text("fixed\n")
        for args in (["git", "add", "-A"], ["git", "commit", "-m", "fix(worker): retry"],
                     ["git", "push"]):
            result = subprocess.run(args, env=env, cwd=str(self.tree.path),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{args}: {result.stderr}")

    def test_terminal_a_in_the_same_repo_is_unaffected(self) -> None:
        """The engineer's own checkout keeps working normally and untouched."""
        env = {key: value for key, value in os.environ.items() if not key.startswith("CODER_AI_")}
        before = pr_repo.git(self.clone, "rev-parse", "HEAD")
        result = subprocess.run(["git", "status", "--porcelain"], env=env,
                                cwd=str(self.clone), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(pr_repo.git(self.clone, "rev-parse", "HEAD"), before)

    def test_the_pr_session_cannot_reach_terminal_as_repository(self) -> None:
        env = {**os.environ, **self.guard.env}
        result = subprocess.run(["git", "status"], env=env, cwd=str(self.clone),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 13)
        self.assertIn("DENY_OUTSIDE_WORKTREE", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
