"""A scriptable fake `gh` that serves gh-shaped JSON for one pull request.

Lets snapshot/delta/findings/watch tests drive a realistic PR conversation
(comments arriving, reviews submitted, threads resolved, CI flipping, new HEAD)
without touching the network.
"""

from __future__ import annotations

import json
from typing import Any

from coderai.pr_automation.github import GhResult


class FakeGitHub:
    def __init__(self, number: int = 1420, owner: str = "org", repo: str = "aiila",
                 head_branch: str = "feature/worker-retry") -> None:
        self.owner, self.repo, self.number = owner, repo, number
        self.calls: list[tuple[str, ...]] = []
        self.failures: dict[str, tuple[int, str]] = {}
        self._counter = 0
        self.pr: dict[str, Any] = {
            "number": number,
            "title": "Fix worker retry handling",
            "body": "Retry handling for the worker queue. Refs #77",
            "author": {"login": "nobin"},
            "state": "OPEN",
            "isDraft": False,
            "url": f"https://github.com/{owner}/{repo}/pull/{number}",
            "headRefName": head_branch,
            "baseRefName": "main",
            "headRefOid": "abc1230000000000000000000000000000000000",
            "mergeable": "MERGEABLE",
            "isCrossRepository": False,
            "createdAt": "2026-09-01T09:00:00Z",
            "updatedAt": "2026-09-01T09:00:00Z",
            "comments": [],
            "reviews": [],
            "commits": [{"oid": "abc1230000000000000000000000000000000000",
                         "messageHeadline": "fix(worker): retry", "committedDate":
                         "2026-09-01T09:00:00Z", "authors": [{"login": "nobin"}]}],
            "files": [{"path": "worker/retry.py"}],
            "statusCheckRollup": [],
            "labels": [],
            "reviewRequests": [],
        }
        self.threads: list[dict[str, Any]] = []

    # ---- scripting helpers -------------------------------------------
    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self._counter:04d}"

    def add_comment(self, author: str, body: str, *, bot: bool = False,
                    at: str = "2026-09-01T10:00:00Z") -> str:
        identifier = self._next("C_")
        self.pr["comments"].append({
            "id": identifier, "author": {"login": author, "__typename":
                                         "Bot" if bot else "User"},
            "body": body, "createdAt": at, "updatedAt": at,
            "url": f"{self.pr['url']}#issuecomment-{identifier}",
        })
        self.pr["updatedAt"] = at
        return identifier

    def edit_comment(self, identifier: str, body: str,
                     at: str = "2026-09-01T11:00:00Z") -> None:
        for item in self.pr["comments"]:
            if item["id"] == identifier:
                item["body"] = body
                item["updatedAt"] = at

    def delete_comment(self, identifier: str) -> None:
        self.pr["comments"] = [item for item in self.pr["comments"]
                               if item["id"] != identifier]

    def add_review(self, author: str, state: str, body: str = "",
                   at: str = "2026-09-01T10:05:00Z", bot: bool = False) -> str:
        identifier = self._next("R_")
        self.pr["reviews"].append({
            "id": identifier,
            "author": {"login": author, "__typename": "Bot" if bot else "User"},
            "state": state, "body": body, "submittedAt": at,
        })
        return identifier

    def add_thread(self, author: str, body: str, path: str = "worker/retry.py",
                   line: int = 12, *, at: str = "2026-09-01T10:02:00Z",
                   bot: bool = False) -> str:
        thread_id = self._next("T_")
        self.threads.append({
            "id": thread_id, "isResolved": False, "resolvedBy": None,
            "isOutdated": False,
            "isCollapsed": False, "path": path, "line": line, "originalLine": line,
            "comments": {"nodes": [{
                "id": f"RC_{thread_id}", "databaseId": self._counter * 100,
                "body": body, "createdAt": at, "updatedAt": at,
                "url": f"{self.pr['url']}#discussion_r{self._counter}",
                "author": {"login": author, "__typename": "Bot" if bot else "User"},
                "path": path, "line": line, "originalLine": line, "outdated": False,
            }]},
        })
        return thread_id

    def reply_in_thread(self, thread_id: str, author: str, body: str,
                        at: str = "2026-09-01T10:30:00Z") -> None:
        for thread in self.threads:
            if thread["id"] == thread_id:
                self._counter += 1
                thread["comments"]["nodes"].append({
                    "id": f"RC_{thread_id}_{self._counter}",
                    "databaseId": self._counter * 100, "body": body,
                    "createdAt": at, "updatedAt": at, "url": "",
                    "author": {"login": author, "__typename": "User"},
                    "path": thread["path"], "line": thread["line"],
                    "originalLine": thread["line"], "outdated": False,
                })

    def resolve_thread(self, thread_id: str, by: str = "reviewer") -> None:
        for thread in self.threads:
            if thread["id"] == thread_id:
                thread["isResolved"] = True
                thread["resolvedBy"] = {"login": by}

    def unresolve_thread(self, thread_id: str) -> None:
        """GitHub lets anyone re-open a resolved conversation."""
        for thread in self.threads:
            if thread["id"] == thread_id:
                thread["isResolved"] = False
                thread["resolvedBy"] = None

    def delete_thread(self, thread_id: str) -> None:
        """A reviewer deleting their own comment removes the thread entirely."""
        self.threads = [item for item in self.threads if item["id"] != thread_id]

    def outdate_thread(self, thread_id: str) -> None:
        for thread in self.threads:
            if thread["id"] == thread_id:
                thread["isOutdated"] = True

    def set_check(self, name: str, status: str, conclusion: str = "",
                  required: bool = True, workflow: str = "ci") -> None:
        for row in self.pr["statusCheckRollup"]:
            if row.get("name") == name:
                row.update({"status": status, "conclusion": conclusion})
                return
        self.pr["statusCheckRollup"].append({
            "name": name, "status": status, "conclusion": conclusion,
            "workflowName": workflow, "isRequired": required,
            "detailsUrl": f"https://github.com/{self.owner}/{self.repo}/actions/runs/1",
        })

    def push_commit(self, sha: str, message: str = "fix(worker): idempotent retry",
                    at: str = "2026-09-01T12:00:00Z") -> None:
        self.pr["headRefOid"] = sha
        self.pr["commits"].append({"oid": sha, "messageHeadline": message,
                                   "committedDate": at, "authors": [{"login": "nobin"}]})
        self.pr["updatedAt"] = at

    def merge(self) -> None:
        self.pr["state"] = "MERGED"

    def close(self) -> None:
        self.pr["state"] = "CLOSED"

    # ---- the gh interface --------------------------------------------
    def __call__(self, argv, cwd, timeout) -> GhResult:
        argv = list(argv)
        self.calls.append(tuple(argv))
        key = " ".join(argv[1:3])
        if key in self.failures:
            code, message = self.failures[key]
            return GhResult(tuple(argv), code, "", message, 5)
        if argv[1:3] == ["pr", "view"]:
            requested = next((item for item in argv[3:] if item.isdigit()), None)
            if requested is not None and int(requested) != self.number:
                return GhResult(tuple(argv), 1, "",
                                "could not resolve to a PullRequest with the number "
                                f"of {requested}", 5)
            return GhResult(tuple(argv), 0, json.dumps(self.pr), "", 5)
        if argv[1:3] == ["api", "graphql"]:
            payload = {"data": {"repository": {"pullRequest": {
                "reviewThreads": {"nodes": self.threads}}}}}
            return GhResult(tuple(argv), 0, json.dumps(payload), "", 5)
        if argv[1:3] == ["repo", "view"]:
            return GhResult(tuple(argv), 0, json.dumps({
                "name": self.repo, "owner": {"login": self.owner},
                "url": f"https://github.com/{self.owner}/{self.repo}"}), "", 5)
        return GhResult(tuple(argv), 1, "", f"unexpected gh call: {argv}", 5)
