"""Task 02 — repository and PR resolution. Fixtures only; never the network."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from coderai.pr_automation.github import (
    GH_MISSING, GH_RATE_LIMITED, GH_UNAUTHENTICATED, Gh, GhError, GhResult, classify,
)
from coderai.pr_automation.resolve import (
    AMBIGUOUS_PR, BAD_TARGET, NOT_A_REPO, NO_PR_FOR_BRANCH, PR_ALREADY_CLOSED,
    PR_ALREADY_MERGED, PR_NOT_FOUND, REPO_MISMATCH, ResolveError, parse_target, resolve,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gh"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeGh:
    """Replays recorded `gh` output for known argv shapes."""

    def __init__(self, *, repo: str = "repo-view.json", prs: dict[int, str] | None = None,
                 lists: dict[str, str] | None = None, fail: dict[str, tuple[int, str]] | None = None):
        self.repo = repo
        self.prs = prs or {1420: "pr-1420.json", 1421: "pr-1421-merged.json",
                           1422: "pr-1422-closed.json"}
        self.lists = lists or {"feature/worker-retry": "pr-list-worker-retry.json"}
        self.fail = fail or {}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, cwd, timeout) -> GhResult:
        argv = list(argv)
        self.calls.append(tuple(argv))
        key = " ".join(argv[1:3])
        if key in self.fail:
            code, message = self.fail[key]
            return GhResult(tuple(argv), code, "", message, 3)
        if argv[1:3] == ["repo", "view"]:
            return GhResult(tuple(argv), 0, fixture(self.repo), "", 3)
        if argv[1:3] == ["pr", "view"]:
            number = int(argv[3])
            if number not in self.prs:
                return GhResult(tuple(argv), 1, "", "could not resolve to a PullRequest", 3)
            return GhResult(tuple(argv), 0, fixture(self.prs[number]), "", 3)
        if argv[1:3] == ["pr", "list"]:
            branch = argv[argv.index("--head") + 1]
            return GhResult(tuple(argv), 0,
                            fixture(self.lists.get(branch, "pr-list-empty.json")), "", 3)
        return GhResult(tuple(argv), 1, "", f"unexpected gh call: {argv}", 3)


def git_repo(tmp: Path, remote: str = "https://github.com/org/aiila.git",
             name: str = "origin") -> Path:
    subprocess.run(["git", "init", "-q", str(tmp)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp), "remote", "add", name, remote],
                   check=True, capture_output=True)
    return tmp


class TargetParsing(unittest.TestCase):
    def test_number_forms(self) -> None:
        self.assertEqual(parse_target("1420").number, 1420)
        self.assertEqual(parse_target("#1420").number, 1420)
        self.assertEqual(parse_target("1420").kind, "number")

    def test_branch(self) -> None:
        target = parse_target("feature/worker-retry")
        self.assertEqual((target.kind, target.branch), ("branch", "feature/worker-retry"))

    def test_url(self) -> None:
        target = parse_target("https://github.com/org/aiila/pull/1420")
        self.assertEqual(
            (target.kind, target.host, target.owner, target.repo, target.number),
            ("url", "github.com", "org", "aiila", 1420),
        )

    def test_url_with_trailing_path(self) -> None:
        self.assertEqual(parse_target("https://github.com/org/aiila/pull/1420/files").number, 1420)

    def test_hostile_branch_names_are_refused(self) -> None:
        for value in ("../../etc/passwd", "-rf", "a..b", "a\x00b", "br anch", "x@{1}",
                      "/leading", "trailing/", "a" * 300, "", "https://github.com/org/aiila"):
            with self.subTest(value=value), self.assertRaises(ResolveError) as caught:
                parse_target(value)
            self.assertEqual(caught.exception.reason, BAD_TARGET)

    def test_pr_zero_rejected(self) -> None:
        with self.assertRaises(ResolveError):
            parse_target("0")


class Resolution(unittest.TestCase):
    def _resolve(self, target: str, fake: FakeGh | None = None, **kwargs):
        fake = fake or FakeGh()
        with tempfile.TemporaryDirectory() as tmp:
            path = git_repo(Path(tmp))
            return resolve(target, path, Gh(path, runner=fake), **kwargs)

    def test_number(self) -> None:
        pr = self._resolve("1420")
        self.assertEqual((pr.owner, pr.repo, pr.number), ("org", "aiila", 1420))
        self.assertEqual(pr.head_branch, "feature/worker-retry")
        self.assertEqual(pr.base_branch, "main")
        self.assertEqual(pr.head_sha, "abc123abc123abc123abc123abc123abc123abcd")
        self.assertEqual(pr.state, "OPEN")
        self.assertEqual(pr.slug, "org/aiila#1420")

    def test_branch_resolves_to_the_pr(self) -> None:
        pr = self._resolve("feature/worker-retry")
        self.assertEqual(pr.number, 1420)

    def test_url_resolves(self) -> None:
        pr = self._resolve("https://github.com/org/aiila/pull/1420")
        self.assertEqual(pr.number, 1420)

    def test_url_for_another_repo_is_refused(self) -> None:
        with self.assertRaises(ResolveError) as caught:
            self._resolve("https://github.com/other/thing/pull/7")
        self.assertEqual(caught.exception.reason, REPO_MISMATCH)

    def test_url_on_another_host_is_refused(self) -> None:
        with self.assertRaises(ResolveError) as caught:
            self._resolve("https://ghe.internal/org/aiila/pull/1420")
        self.assertEqual(caught.exception.reason, REPO_MISMATCH)

    def test_branch_without_a_pr(self) -> None:
        with self.assertRaises(ResolveError) as caught:
            self._resolve("feature/no-pr")
        self.assertEqual(caught.exception.reason, NO_PR_FOR_BRANCH)

    def test_ambiguous_branch_lists_both(self) -> None:
        fake = FakeGh(lists={"feature/worker-retry": "pr-list-ambiguous.json"})
        with self.assertRaises(ResolveError) as caught:
            self._resolve("feature/worker-retry", fake)
        self.assertEqual(caught.exception.reason, AMBIGUOUS_PR)
        self.assertIn("#1420", caught.exception.message)
        self.assertIn("#1499", caught.exception.message)

    def test_merged_pr_cannot_start_a_session(self) -> None:
        with self.assertRaises(ResolveError) as caught:
            self._resolve("1421")
        self.assertEqual(caught.exception.reason, PR_ALREADY_MERGED)

    def test_closed_pr_cannot_start_a_session(self) -> None:
        with self.assertRaises(ResolveError) as caught:
            self._resolve("1422")
        self.assertEqual(caught.exception.reason, PR_ALREADY_CLOSED)

    def test_terminal_pr_readable_when_explicitly_allowed(self) -> None:
        pr = self._resolve("1421", allow_terminal=True)
        self.assertEqual(pr.state, "MERGED")

    def test_unknown_pr(self) -> None:
        with self.assertRaises(ResolveError) as caught:
            self._resolve("9999")
        self.assertEqual(caught.exception.reason, PR_NOT_FOUND)

    def test_not_a_github_repo(self) -> None:
        fake = FakeGh(fail={"repo view": (1, "none of the git remotes point to a known host")})
        with self.assertRaises(ResolveError) as caught:
            self._resolve("1420", fake)
        self.assertEqual(caught.exception.reason, NOT_A_REPO)

    def test_unauthenticated_is_named_not_swallowed(self) -> None:
        fake = FakeGh(fail={"repo view": (1, "gh auth login required")})
        with self.assertRaises(ResolveError) as caught:
            self._resolve("1420", fake)
        self.assertEqual(caught.exception.reason, GH_UNAUTHENTICATED)

    def test_remote_name_is_discovered_not_assumed(self) -> None:
        fake = FakeGh()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            git_repo(path, "git@github.com:someone/fork.git", "origin")
            subprocess.run(["git", "-C", str(path), "remote", "add", "upstream",
                            "git@github.com:org/aiila.git"], check=True, capture_output=True)
            pr = resolve("1420", path, Gh(path, runner=fake))
        self.assertEqual(pr.remote, "upstream")

    def test_no_shell_interpolation_anywhere(self) -> None:
        fake = FakeGh()
        self._resolve("1420", fake)
        for call in fake.calls:
            self.assertEqual(call[0], "gh")
            self.assertTrue(all(isinstance(item, str) for item in call))


class GhWrapper(unittest.TestCase):
    def test_missing_gh_is_named(self) -> None:
        def missing(argv, cwd, timeout):
            raise FileNotFoundError(argv[0])

        client = Gh(Path("."), runner=missing)
        with self.assertRaises(GhError) as caught:
            client.run("pr", "view", "1")
        self.assertEqual(caught.exception.reason, GH_MISSING)
        self.assertIn("cli.github.com", str(caught.exception))

    def test_classification(self) -> None:
        cases = {
            "gh: To get started with GitHub CLI, please run: gh auth login": GH_UNAUTHENTICATED,
            "API rate limit exceeded for user": GH_RATE_LIMITED,
            "Could not resolve host: api.github.com": "GH_NETWORK",
            "could not resolve to a PullRequest": "GH_NOT_FOUND",
            "something else entirely": "GH_FAILED",
        }
        for stderr, expected in cases.items():
            with self.subTest(stderr=stderr):
                self.assertEqual(classify(GhResult((), 1, "", stderr, 1)), expected)

    def test_non_json_output_is_an_error_not_a_crash(self) -> None:
        def html(argv, cwd, timeout):
            return GhResult(tuple(argv), 0, "<html>proxy error</html>", "", 1)

        with self.assertRaises(GhError):
            Gh(Path("."), runner=html).json("pr", "view", "1")

    def test_output_is_capped(self) -> None:
        from coderai.pr_automation.github import MAX_OUTPUT
        self.assertLessEqual(MAX_OUTPUT, 8_000_000)


class NoNetwork(unittest.TestCase):
    def test_fixtures_are_valid_json(self) -> None:
        for path in FIXTURES.glob("*.json"):
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
