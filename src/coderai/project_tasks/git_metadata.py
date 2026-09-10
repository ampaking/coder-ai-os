"""Optional, content-free Git metadata collection."""

from __future__ import annotations

import os
import re
import subprocess
import json
from pathlib import Path
from typing import Any

from coderai.project_tasks.project import load_settings, root_hash
from coderai.project_tasks.storage import StorageError, connect


def parse_git_log(output: str) -> list[dict[str, Any]]:
    """Parse hashes, timestamps, and aggregate shortstat values; never file names or contents."""
    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in output.splitlines():
        if "\t" in line and len(line.split("\t", 1)[0]) == 40:
            digest, parents, committed = line.split("\t", 2)
            current = {"hash": digest, "parents": parents, "at": committed, "add": 0, "del": 0, "files": 0}
            commits.append(current)
        elif current and line.strip():
            files = re.search(r"(\d+) files? changed", line)
            additions = re.search(r"(\d+) insertions?\(\+\)", line)
            deletions = re.search(r"(\d+) deletions?\(-\)", line)
            current["files"] = int(files.group(1)) if files else 0
            current["add"] = int(additions.group(1)) if additions else 0
            current["del"] = int(deletions.group(1)) if deletions else 0
    return commits


def scan_git(project: Path, limit: int = 200) -> dict[str, int]:
    settings = load_settings(project) or {}
    if not settings.get("gitMetadata"):
        raise StorageError("Git metadata is off; enable it with: coder-ai tasks config --git-metadata on")
    if not 1 <= limit <= 1000:
        raise StorageError("Git scan limit must be between 1 and 1000")
    git_env = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"}
    head = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--verify", "HEAD"],
        check=False, capture_output=True, text=True, timeout=5, env=git_env,
    )
    if head.returncode != 0:
        return {"examined": 0, "inserted": 0, "candidateLinks": 0}
    result = subprocess.run(
        ["git", "-C", str(project), "log", f"-{limit}", "--format=%H%x09%P%x09%cI", "--shortstat"],
        check=False, capture_output=True, text=True, timeout=15, env=git_env,
    )
    if result.returncode != 0:
        raise StorageError("unable to read Git metadata")
    commits = parse_git_log(result.stdout)
    with connect(project) as connection:
        before = connection.total_changes
        for item in commits:
            connection.execute(
                "INSERT OR IGNORE INTO commits(hash,project_hash,parent_hashes,committed_at,additions,deletions,file_count) VALUES(?,?,?,?,?,?,?)",
                (item["hash"], root_hash(project), item["parents"], item["at"], item["add"], item["del"], item["files"]),
            )
        inserted = connection.total_changes - before
        linked_before = connection.total_changes
        connection.execute(
            "INSERT OR IGNORE INTO task_commits(task_id,commit_hash,confidence,evidence_json) "
            "SELECT tasks.id,commits.hash,0.45,'[\"commit timestamp overlaps task activity window\"]' "
            "FROM tasks JOIN commits ON commits.project_hash=tasks.project_hash "
            "WHERE tasks.project_hash=? AND commits.committed_at>=tasks.started_at "
            "AND commits.committed_at<=COALESCE(tasks.completed_at,tasks.updated_at)",
            (root_hash(project),),
        )
        linked = connection.total_changes - linked_before
    return {"examined": len(commits), "inserted": inserted, "candidateLinks": linked}


def list_git_links(project: Path) -> list[dict[str, Any]]:
    with connect(project) as connection:
        rows = connection.execute(
            "SELECT task_commits.task_id AS taskId,tasks.title AS taskTitle,task_commits.commit_hash AS commitHash,"
            "task_commits.confidence,task_commits.status,task_commits.evidence_json,commits.committed_at AS committedAt,"
            "commits.file_count AS fileCount,commits.additions,commits.deletions FROM task_commits "
            "JOIN tasks ON tasks.id=task_commits.task_id JOIN commits ON commits.hash=task_commits.commit_hash "
            "WHERE tasks.project_hash=? ORDER BY commits.committed_at DESC", (root_hash(project),)
        ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["evidence"] = json.loads(item.pop("evidence_json"))
    return result


def set_git_link(project: Path, task_id: str, commit_hash: str, status: str) -> dict[str, Any]:
    if status not in {"candidate", "confirmed", "rejected"}:
        raise StorageError("unsupported Git link status")
    confidence = {"candidate": 0.45, "confirmed": 1.0, "rejected": 0.0}[status]
    evidence = {
        "candidate": '["commit timestamp overlaps task activity window"]',
        "confirmed": '["user confirmed task-to-commit relationship"]',
        "rejected": '["user rejected task-to-commit relationship"]',
    }[status]
    with connect(project) as connection:
        if connection.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
            raise StorageError("task not found")
        if connection.execute("SELECT 1 FROM commits WHERE hash=?", (commit_hash,)).fetchone() is None:
            raise StorageError("commit metadata not found; run: coder-ai tasks git")
        connection.execute(
            "INSERT INTO task_commits(task_id,commit_hash,confidence,evidence_json,status,corrected) "
            "VALUES(?,?,?,?,?,1) ON CONFLICT(task_id,commit_hash) DO UPDATE SET confidence=excluded.confidence,"
            "evidence_json=excluded.evidence_json,status=excluded.status,corrected=1",
            (task_id, commit_hash, confidence, evidence, status),
        )
    return next(item for item in list_git_links(project)
                if item["taskId"] == task_id and item["commitHash"] == commit_hash)
