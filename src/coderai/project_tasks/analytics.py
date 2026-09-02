"""Evidence-based review, idea, graph, and export projections."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from coderai.project_tasks.project import load_settings, root_hash
from coderai.project_tasks.storage import connect


def _review_zone(project: Path, requested_timezone: str | None = None) -> tuple[Any, str]:
    settings = load_settings(project) or {}
    timezone_name = requested_timezone or str(settings.get("timeZone", "local"))
    try:
        zone = datetime.now().astimezone().tzinfo if timezone_name == "local" else ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return UTC, "UTC"
    return zone, timezone_name


def recent_activity(project: Path, limit: int = 30) -> list[dict[str, Any]]:
    """Return a bounded, structured timeline suitable for human explanation."""
    with connect(project) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT task_events.id,task_events.task_id AS taskId,tasks.title AS taskTitle,tasks.theme,"
            "tasks.status,task_events.event_type AS type,task_events.summary,task_events.occurred_at AS occurredAt,"
            "sessions.agent_name AS agentName,sessions.model_name AS modelName FROM task_events "
            "JOIN tasks ON tasks.id=task_events.task_id LEFT JOIN sessions ON sessions.id=task_events.session_id "
            "WHERE task_events.project_hash=? ORDER BY task_events.sequence DESC LIMIT ?",
            (root_hash(project), min(max(limit, 1), 100)),
        )]


def task_evidence_summaries(project: Path) -> list[dict[str, Any]]:
    """Project tasks with the latest lifecycle, open-blocker, and validation evidence."""
    with connect(project) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT tasks.*,"
            "(SELECT event_type FROM task_events WHERE task_id=tasks.id ORDER BY sequence DESC LIMIT 1) AS latestEvent,"
            "(SELECT summary FROM task_events WHERE task_id=tasks.id ORDER BY sequence DESC LIMIT 1) AS latestSummary,"
            "(SELECT occurred_at FROM task_events WHERE task_id=tasks.id ORDER BY sequence DESC LIMIT 1) AS latestAt,"
            "(SELECT COUNT(*) FROM blockers WHERE task_id=tasks.id AND status='open') AS openBlockers,"
            "(SELECT outcome FROM validations WHERE task_id=tasks.id ORDER BY occurred_at DESC LIMIT 1) AS latestProof "
            "FROM tasks WHERE tasks.project_hash=? ORDER BY tasks.updated_at DESC",
            (root_hash(project),),
        )]


def _source_status(count: int, freshest_at: str | None) -> str:
    if not count:
        return "missing"
    if not freshest_at:
        return "stale"
    freshest = datetime.fromisoformat(freshest_at)
    if freshest.tzinfo is None:
        freshest = freshest.replace(tzinfo=UTC)
    current = datetime.now(UTC)
    if freshest > current + timedelta(days=1):
        return "future-clock-skew"
    return "connected" if freshest >= current - timedelta(days=30) else "stale"


def project_intelligence(project: Path) -> dict[str, Any]:
    """Derive bounded project-health evidence without persisting source payloads."""
    settings = load_settings(project) or {}
    with connect(project) as connection:
        event_count, latest_event = connection.execute(
            "SELECT COUNT(*),MAX(occurred_at) FROM task_events WHERE project_hash=?", (root_hash(project),)
        ).fetchone()
        validation_count, latest_validation = connection.execute(
            "SELECT COUNT(*),MAX(validations.occurred_at) FROM validations JOIN tasks ON tasks.id=validations.task_id "
            "WHERE tasks.project_hash=?", (root_hash(project),)
        ).fetchone()
        observation_count, latest_observation = connection.execute(
            "SELECT COUNT(*),MAX(occurred_at) FROM observations WHERE project_hash=? AND source='repository-checks'",
            (root_hash(project),),
        ).fetchone()
        link_count, latest_link = connection.execute(
            "SELECT COUNT(*),MAX(task_links.created_at) FROM task_links JOIN tasks ON tasks.id=task_links.task_id WHERE tasks.project_hash=?",
            (root_hash(project),),
        ).fetchone()
        commit_count, latest_commit = connection.execute(
            "SELECT COUNT(*),MAX(committed_at) FROM commits WHERE project_hash=?", (root_hash(project),)
        ).fetchone()
        active = [dict(row) for row in connection.execute(
            "SELECT id,title,status,confidence,updated_at AS updatedAt FROM tasks WHERE project_hash=? "
            "AND status IN ('active','blocked','needs_validation') "
            "ORDER BY CASE status WHEN 'blocked' THEN 0 WHEN 'needs_validation' THEN 1 ELSE 2 END,updated_at LIMIT 20",
            (root_hash(project),),
        )]
        latest_failed = connection.execute(
            "SELECT tasks.id AS taskId,tasks.title,tasks.confidence,validations.occurred_at AS occurredAt FROM validations "
            "JOIN tasks ON tasks.id=validations.task_id WHERE tasks.project_hash=? AND validations.outcome='failed' "
            "AND validations.occurred_at=(SELECT MAX(v2.occurred_at) FROM validations v2 WHERE v2.task_id=tasks.id) "
            "ORDER BY validations.occurred_at DESC LIMIT 1", (root_hash(project),)
        ).fetchone()
    sources = [
        {"id": "lifecycle", "label": "Task lifecycle", "status": _source_status(event_count, latest_event),
         "records": event_count, "freshestAt": latest_event, "stores": "bounded task events"},
        {"id": "validation", "label": "Validation proof", "status": _source_status(validation_count, latest_validation),
         "records": validation_count, "freshestAt": latest_validation, "stores": "outcomes and command summaries"},
        {"id": "relationships", "label": "Task relationships", "status": _source_status(link_count, latest_link),
         "records": link_count, "freshestAt": latest_link, "stores": "typed task links"},
        {"id": "git", "label": "Optional Git evidence",
         "status": _source_status(commit_count, latest_commit) if settings.get("gitMetadata") else "disabled",
         "records": commit_count, "freshestAt": latest_commit, "stores": "content-free commit aggregates"},
        {"id": "repository-checks", "label": "Repository health checks",
         "status": _source_status(observation_count, latest_observation),
         "records": observation_count, "freshestAt": latest_observation,
         "stores": "content-free outcomes from approved validation events"},
    ]
    source_status = {source["id"]: source["status"] for source in sources}
    signals: list[dict[str, Any]] = []
    for task in active:
        kind = {"blocked": "blocker", "needs_validation": "proof-gap", "active": "active-work"}[task["status"]]
        signals.append({
            "id": f"{kind}:{task['id']}", "kind": kind, "taskId": task["id"],
            "title": {"blocked": "Recorded blocker", "needs_validation": "Proof still needed",
                      "active": "Active work should continue"}[task["status"]],
            "detail": task["title"], "severity": "attention" if task["status"] != "active" else "information",
            "confidence": task["confidence"], "sourceIds": ["lifecycle"],
            "observedAt": task["updatedAt"],
        })
    if latest_failed and not active and source_status["validation"] == "connected":
        signals.append({
            "id": f"failed-proof:{latest_failed['taskId']}", "kind": "failed-proof",
            "taskId": latest_failed["taskId"], "title": "Latest proof failed",
            "detail": latest_failed["title"], "severity": "attention", "confidence": latest_failed["confidence"],
            "sourceIds": ["validation"], "observedAt": latest_failed["occurredAt"],
        })
    if active and source_status["lifecycle"] != "connected":
        focus = active[0]
        signals.append({
            "id": "coverage:lifecycle", "kind": "coverage-gap", "taskId": focus["id"],
            "title": "Task state evidence needs refresh", "detail": focus["title"],
            "severity": "attention", "confidence": 0.8, "sourceIds": ["lifecycle"],
            "observedAt": next(source["freshestAt"] for source in sources if source["id"] == "lifecycle"),
        })
        recommendation = {"id": "next-state-refresh", "title": f"Revalidate {focus['title']}",
                          "reason": "The recorded active task exists, but its lifecycle evidence is stale or time-skewed.",
                          "taskId": focus["id"], "signalIds": ["coverage:lifecycle"], "confidence": 0.6,
                          "decision": "existing-task-needs-refresh", "mode": "insufficient-data"}
    elif active:
        focus = active[0]
        action = "Resolve" if focus["status"] == "blocked" else "Prove" if focus["status"] == "needs_validation" else "Continue"
        recommendation = {"id": "next-active-work", "title": f"{action} {focus['title']}",
                          "reason": "Active work takes priority over discovering new work.",
                          "taskId": focus["id"], "signalIds": [signals[0]["id"]],
                          "confidence": min(0.9, float(signals[0]["confidence"])),
                          "decision": "existing-task", "mode": "active"}
    elif signals:
        recommendation = {"id": "next-risk-review", "title": f"Investigate {signals[0]['detail']}",
                          "reason": "No active task exists and the latest proof is failing.",
                          "taskId": signals[0].get("taskId"), "signalIds": [signals[0]["id"]],
                          "confidence": min(0.9, float(signals[0]["confidence"])),
                          "decision": "proposal-needs-acceptance", "mode": "idle"}
    elif source_status["validation"] != "connected":
        signals.append({
            "id": "coverage:validation", "kind": "coverage-gap", "taskId": None,
            "title": "Validation evidence needs connection or refresh", "detail": "Validation proof",
            "severity": "attention", "confidence": 1.0, "sourceIds": ["validation"],
            "observedAt": next(source["freshestAt"] for source in sources if source["id"] == "validation"),
        })
        recommendation = {"id": "next-evidence-coverage", "title": "Connect validation evidence",
                          "reason": "No active task and no current validation proof are available; bug discovery would be guesswork.",
                          "taskId": None, "signalIds": ["coverage:validation"], "confidence": 0.9,
                          "decision": "proposal-needs-acceptance", "mode": "insufficient-data"}
    else:
        signals.append({
            "id": "opportunity:idle", "kind": "opportunity", "taskId": None,
            "title": "No active work or strong failure signal", "detail": "Current lifecycle and validation evidence",
            "severity": "information", "confidence": 0.6, "sourceIds": ["lifecycle", "validation"],
            "observedAt": latest_event or latest_validation,
        })
        recommendation = {"id": "next-opportunity-review", "title": "Review evidence for the next small improvement",
                          "reason": "No active task or strong failure signal is recorded.",
                          "taskId": None, "signalIds": ["opportunity:idle"], "confidence": 0.6,
                          "decision": "proposal-needs-acceptance", "mode": "idle"}
    connected = sum(source["status"] == "connected" for source in sources)
    return {
        "mode": recommendation["mode"], "sources": sources, "signals": signals,
        "recommendation": recommendation,
        "coverage": {"status": "sufficient-for-active-work" if active and sources[0]["status"] == "connected" else
                     "partial" if connected else "insufficient", "connectedSources": connected,
                     "totalSources": len(sources), "caution": "Missing evidence is unknown, not poor performance."},
        "privacy": ["Collection policy excludes prompts, model answers, source contents, and file paths",
                    "Agent adapters redact detected credentials; direct structured-event text remains caller-provided",
                    "No employee scoring"],
    }


def period_insights(project: Path, period: str = "week", anchor: str | None = None,
                    time_zone: str | None = None) -> dict[str, Any]:
    """Aggregate inspectable lifecycle evidence for day, week, month, or year."""
    if period not in {"day", "week", "month", "year"}:
        raise ValueError("period must be day, week, month, or year")
    zone, timezone_name = _review_zone(project, time_zone)
    local_now = datetime.now(zone)
    if anchor:
        try:
            anchor_date = datetime.strptime(anchor, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("anchor must be a YYYY-MM-DD date") from exc
        selected = datetime.combine(anchor_date, datetime.min.time(), tzinfo=zone)
    else:
        selected = local_now
    if period == "day":
        local_start = selected.replace(hour=0, minute=0, second=0, microsecond=0)
        boundaries = [local_start + timedelta(hours=4 * index) for index in range(7)]
        labels = [value.strftime("%H:%M") for value in boundaries[:-1]]
    elif period == "week":
        local_start = (selected - timedelta(days=selected.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        boundaries = [local_start + timedelta(days=index) for index in range(8)]
        labels = [value.strftime("%a") for value in boundaries[:-1]]
    elif period == "month":
        local_start = selected.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_month = (local_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        boundaries = [local_start]
        while boundaries[-1] < next_month:
            boundaries.append(min(boundaries[-1] + timedelta(days=7), next_month))
        labels = [f"{value.day}–{(boundaries[index + 1] - timedelta(days=1)).day}" for index, value in enumerate(boundaries[:-1])]
    else:
        local_start = selected.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        boundaries = [local_start]
        for month in range(2, 13):
            boundaries.append(local_start.replace(month=month))
        boundaries.append(local_start.replace(year=local_start.year + 1))
        labels = [value.strftime("%b") for value in boundaries[:-1]]
    local_end = boundaries[-1]
    query_end = local_now if local_start <= local_now < local_end else local_end
    start_utc = local_start.astimezone(UTC).isoformat()
    end_utc = query_end.astimezone(UTC).isoformat()
    period_duration = local_end - local_start
    elapsed_duration = query_end - local_start
    previous_start = local_start - period_duration
    previous_end = previous_start + elapsed_duration
    previous_start_utc = previous_start.astimezone(UTC).isoformat()
    previous_end_utc = previous_end.astimezone(UTC).isoformat()
    with connect(project) as connection:
        activity = [dict(row) for row in connection.execute(
            "SELECT task_events.id,task_events.task_id AS taskId,tasks.title AS taskTitle,"
            "task_events.event_type AS type,task_events.summary,task_events.occurred_at AS occurredAt,"
            "sessions.agent_name AS agentName FROM task_events JOIN tasks ON tasks.id=task_events.task_id "
            "LEFT JOIN sessions ON sessions.id=task_events.session_id WHERE task_events.project_hash=? "
            "AND task_events.occurred_at>=? AND task_events.occurred_at<? "
            "ORDER BY task_events.occurred_at DESC,task_events.sequence DESC LIMIT 500",
            (root_hash(project), start_utc, end_utc),
        )]
        completed = [dict(row) for row in connection.execute(
            "SELECT id,title,theme,completed_at AS completedAt FROM tasks WHERE project_hash=? "
            "AND completed_at>=? AND completed_at<? ORDER BY completed_at DESC LIMIT 100",
            (root_hash(project), start_utc, end_utc),
        )]
        observed_activity = [dict(row) for row in connection.execute(
            "SELECT id,kind AS type,outcome,summary,occurred_at AS occurredAt FROM observations "
            "WHERE project_hash=? AND occurred_at>=? AND occurred_at<? "
            "ORDER BY occurred_at DESC LIMIT 500",
            (root_hash(project), start_utc, end_utc),
        )]
        observation_total = connection.execute(
            "SELECT COUNT(*) FROM observations WHERE project_hash=? AND occurred_at>=? AND occurred_at<?",
            (root_hash(project), start_utc, end_utc),
        ).fetchone()[0]
        activity_total = connection.execute(
            "SELECT COUNT(*) FROM task_events WHERE project_hash=? AND occurred_at>=? AND occurred_at<?",
            (root_hash(project), start_utc, end_utc),
        ).fetchone()[0]
        completed_total = connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_hash=? AND completed_at>=? AND completed_at<?",
            (root_hash(project), start_utc, end_utc),
        ).fetchone()[0]
        open_counts = [dict(row) for row in connection.execute(
            "SELECT status,COUNT(*) AS count FROM tasks WHERE project_hash=? "
            "AND status IN ('active','blocked','needs_validation','paused') GROUP BY status",
            (root_hash(project),),
        )]
        previous_event_counts = {row["event_type"]: row["count"] for row in connection.execute(
            "SELECT event_type,COUNT(*) AS count FROM task_events WHERE project_hash=? "
            "AND occurred_at>=? AND occurred_at<? GROUP BY event_type",
            (root_hash(project), previous_start_utc, previous_end_utc),
        )}
        previous_completed = connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_hash=? AND completed_at>=? AND completed_at<?",
            (root_hash(project), previous_start_utc, previous_end_utc),
        ).fetchone()[0]
        bucket_type_counts = []
        for bucket_start, bucket_end in zip(boundaries[:-1], boundaries[1:]):
            effective_end = min(bucket_end, query_end)
            rows = [] if bucket_start >= effective_end else connection.execute(
                "SELECT event_type,COUNT(*) AS count FROM task_events WHERE project_hash=? "
                "AND occurred_at>=? AND occurred_at<? GROUP BY event_type",
                (root_hash(project), bucket_start.astimezone(UTC).isoformat(),
                 effective_end.astimezone(UTC).isoformat()),
            ).fetchall()
            bucket_type_counts.append({row["event_type"]: row["count"] for row in rows})
    for item in activity:
        item["occurredLocal"] = datetime.fromisoformat(item["occurredAt"]).astimezone(zone).isoformat()
    for item in observed_activity:
        item.update({"taskId": None, "taskTitle": "Project evidence", "agentName": None,
                     "occurredLocal": datetime.fromisoformat(item["occurredAt"]).astimezone(zone).isoformat(),
                     "evidenceOnly": True})
    buckets = []
    for index, bucket_start in enumerate(boundaries[:-1]):
        bucket_end = boundaries[index + 1]
        bucket_events = [item for item in activity if bucket_start <= datetime.fromisoformat(item["occurredAt"]).astimezone(zone) < bucket_end]
        bucket_observations = [item for item in observed_activity if bucket_start <= datetime.fromisoformat(item["occurredAt"]).astimezone(zone) < bucket_end]
        counts = {"started": 0, "completed": 0, "blocked": 0, "passed": 0, "failed": 0, "other": 0}
        mapping = {"task_started": "started", "task_completed": "completed", "task_blocked": "blocked",
                   "validation_passed": "passed", "validation_failed": "failed"}
        for event_type, count in bucket_type_counts[index].items():
            if event_type in mapping:
                counts[mapping[event_type]] += count
            else:
                counts["other"] += count
        total_events = sum(bucket_type_counts[index].values())
        buckets.append({"id": f"{period}-{index}", "label": labels[index],
                        "start": bucket_start.isoformat(), "end": bucket_end.isoformat(),
                        **counts, "totalEvents": total_events,
                        "automaticObservations": len(bucket_observations),
                        "automaticPassed": sum(item.get("outcome") == "passed" for item in bucket_observations),
                        "automaticFailed": sum(item.get("outcome") == "failed" for item in bucket_observations),
                        "totalEvidence": total_events + len(bucket_observations),
                        "eventIds": [item["id"] for item in bucket_events],
                        "observationIds": [item["id"] for item in bucket_observations]})
    totals = {key: sum(bucket[key] for bucket in buckets)
              for key in ("started", "completed", "blocked", "passed", "failed", "other", "totalEvents")}
    totals["automaticObservations"] = sum(bucket["automaticObservations"] for bucket in buckets)
    totals["automaticPassed"] = sum(bucket["automaticPassed"] for bucket in buckets)
    totals["automaticFailed"] = sum(bucket["automaticFailed"] for bucket in buckets)
    totals["totalEvidence"] = totals["totalEvents"] + totals["automaticObservations"]
    totals["completedTasks"] = completed_total
    previous_totals = {
        "started": previous_event_counts.get("task_started", 0),
        "completed": previous_event_counts.get("task_completed", 0),
        "blocked": previous_event_counts.get("task_blocked", 0),
        "passed": previous_event_counts.get("validation_passed", 0),
        "failed": previous_event_counts.get("validation_failed", 0),
        "totalEvents": sum(previous_event_counts.values()),
        "completedTasks": previous_completed,
    }
    attention_total = totals["blocked"] + totals["failed"] + totals["automaticFailed"]
    outcome_total = totals["completed"] + totals["passed"] + totals["automaticPassed"]
    if attention_total:
        guidance = {"headline": "Resolve the strongest attention signal",
                    "explanation": f"{attention_total} blocker or failed-proof signal(s) appeared in this period.",
                    "nextAction": "Select the highlighted period, inspect its evidence, and choose the smallest corrective task."}
    elif outcome_total:
        guidance = {"headline": "Build on verified progress",
                    "explanation": f"{outcome_total} completed-outcome or passing-proof signal(s) were recorded.",
                    "nextAction": "Inspect remaining active work and attach proof to the next smallest outcome."}
    elif observation_total:
        guidance = {"headline": "Turn activity into an inspectable outcome",
                    "explanation": f"{observation_total} background activity observation(s) exist without a strong outcome signal.",
                    "nextAction": "Name the current outcome and record validation when its result is known."}
    else:
        guidance = {"headline": "Create the next evidence point",
                    "explanation": "No project evidence was recorded in this selected period.",
                    "nextAction": "Start one small outcome or select another period before deciding what changed."}
    guidance["caution"] = "Activity volume is context, not a productivity score; missing evidence is unknown."
    return {"period": period, "anchor": selected.date().isoformat(),
            "periodStart": local_start.isoformat(), "periodEnd": local_end.isoformat(),
            "isCurrent": local_start <= local_now < local_end,
            "canMoveNext": local_end <= local_now,
            "timeZone": timezone_name, "buckets": buckets, "totals": totals,
            "activity": sorted(activity + observed_activity, key=lambda item: item["occurredAt"], reverse=True),
            "progressGuidance": guidance,
            "completedTasks": completed, "openByStatus": open_counts,
            "comparison": {"periodStart": previous_start.isoformat(),
                           "periodEnd": previous_end.isoformat(), "totals": previous_totals},
            "bounds": {"activityTotal": activity_total, "activityReturned": len(activity),
                       "activityTruncated": activity_total > len(activity),
                       "completedTotal": completed_total, "completedReturned": len(completed),
                       "completedTruncated": completed_total > len(completed)},
            "evidence": ["structured lifecycle events", "validation outcomes", "task completion timestamps",
                         "content-free automatic observations"]}


def _bounded_graph(
    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], start_id: str, limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["source"], []).append(edge["target"])
        adjacency.setdefault(edge["target"], []).append(edge["source"])
    ordered: list[str] = []
    queued = {start_id}
    queue = [start_id]
    while queue and len(ordered) < limit:
        node_id = queue.pop(0)
        if node_id not in nodes:
            continue
        ordered.append(node_id)
        for neighbor in adjacency.get(node_id, []):
            if neighbor not in queued:
                queued.add(neighbor)
                queue.append(neighbor)
    for node_id in nodes:
        if len(ordered) >= limit:
            break
        if node_id not in queued:
            ordered.append(node_id)
    selected = set(ordered)
    coherent_edges = [edge for edge in edges if edge["source"] in selected and edge["target"] in selected]
    bounded_edges = coherent_edges[:limit * 2]
    return ([nodes[node_id] for node_id in ordered], bounded_edges,
            {"truncated": len(nodes) > len(ordered) or len(edges) > len(bounded_edges),
             "totalNodes": len(nodes), "totalEdges": len(edges),
             "returnedNodes": len(ordered), "returnedEdges": len(bounded_edges)})


def task_graph(project: Path, limit: int = 300, intelligence: dict[str, Any] | None = None) -> dict[str, Any]:
    intelligence = intelligence or project_intelligence(project)
    with connect(project) as connection:
        tasks = [dict(row) for row in connection.execute(
            "SELECT id,title,theme,status,updated_at AS updatedAt FROM tasks WHERE project_hash=? ORDER BY updated_at DESC LIMIT ?",
            (root_hash(project), min(limit, 500)),
        )]
        task_ids = {item["id"] for item in tasks}
        selected_ids = tuple(task_ids)
        placeholders = ",".join("?" for _ in selected_ids)
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for task in tasks:
            node_id = f"task:{task['id']}"
            nodes[node_id] = {"id": node_id, "entityId": task["id"], "type": "task",
                              "label": task["title"], "status": task["status"], "updatedAt": task["updatedAt"]}
            if task["theme"]:
                theme_id = "theme:" + hashlib.sha256(task["theme"].casefold().encode()).hexdigest()[:16]
                nodes.setdefault(theme_id, {"id": theme_id, "type": "theme", "label": task["theme"], "status": ""})
                edges.append({"source": theme_id, "target": node_id, "type": "groups", "confidence": 1.0})
        related_rows = connection.execute(
            f"SELECT task_id,related_task_id,link_type FROM task_links WHERE task_id IN ({placeholders}) LIMIT ?",
            (*selected_ids, limit * 2),
        ) if selected_ids else []
        for row in related_rows:
            if row["task_id"] in task_ids and row["related_task_id"] in task_ids:
                edges.append({"source": f"task:{row['task_id']}", "target": f"task:{row['related_task_id']}",
                              "type": row["link_type"], "confidence": 1.0})
        session_rows = connection.execute(
            "SELECT DISTINCT sessions.id,sessions.agent_name,sessions.model_name,task_events.task_id "
            "FROM sessions JOIN task_events ON task_events.session_id=sessions.id "
            f"WHERE task_events.task_id IN ({placeholders}) LIMIT ?", (*selected_ids, limit * 2),
        ) if selected_ids else []
        for row in session_rows:
            if row["task_id"] not in task_ids:
                continue
            node_id = f"session:{row['id']}"
            label = row["agent_name"] + (f" · {row['model_name']}" if row["model_name"] else "")
            nodes.setdefault(node_id, {"id": node_id, "entityId": row["id"], "type": "session", "label": label, "status": ""})
            edges.append({"source": node_id, "target": f"task:{row['task_id']}", "type": "worked_on", "confidence": 1.0})
        blocker_rows = connection.execute(
            "SELECT blockers.id,blockers.task_id,blockers.summary,blockers.status FROM blockers "
            f"WHERE blockers.task_id IN ({placeholders}) LIMIT ?", (*selected_ids, limit * 2),
        ) if selected_ids else []
        for row in blocker_rows:
            if row["task_id"] in task_ids:
                node_id = f"blocker:{row['id']}"
                nodes[node_id] = {"id": node_id, "entityId": row["id"], "type": "blocker", "label": row["summary"], "status": row["status"]}
                edges.append({"source": f"task:{row['task_id']}", "target": node_id, "type": "blocked_by", "confidence": 1.0})
        validation_rows = connection.execute(
            "SELECT validations.id,validations.task_id,validations.category,validations.outcome FROM validations "
            f"WHERE validations.task_id IN ({placeholders}) ORDER BY validations.occurred_at DESC LIMIT ?",
            (*selected_ids, limit * 2),
        ) if selected_ids else []
        for row in validation_rows:
            if row["task_id"] in task_ids:
                node_id = f"validation:{row['id']}"
                nodes[node_id] = {"id": node_id, "entityId": row["id"], "type": "validation",
                                  "label": row["category"], "status": row["outcome"]}
                edges.append({"source": f"task:{row['task_id']}", "target": node_id, "type": "validated_by", "confidence": 1.0})
        commit_rows = connection.execute(
            "SELECT task_commits.task_id,task_commits.commit_hash,task_commits.status,task_commits.confidence "
            f"FROM task_commits WHERE task_commits.task_id IN ({placeholders}) "
            "AND task_commits.status!='rejected' LIMIT ?", (*selected_ids, limit * 2),
        ) if selected_ids else []
        for row in commit_rows:
            if row["task_id"] in task_ids:
                node_id = f"commit:{row['commit_hash']}"
                nodes.setdefault(node_id, {"id": node_id, "entityId": row["commit_hash"], "type": "commit",
                                           "label": row["commit_hash"][:10], "status": row["status"]})
                edges.append({"source": f"task:{row['task_id']}", "target": node_id, "type": "supported_by",
                              "confidence": row["confidence"]})
        for source in intelligence["sources"]:
            node_id = f"source:{source['id']}"
            nodes[node_id] = {"id": node_id, "entityId": source["id"], "type": "source",
                              "label": source["label"], "status": source["status"]}
        for signal in intelligence["signals"]:
            node_id = f"signal:{signal['id']}"
            nodes[node_id] = {"id": node_id, "entityId": signal["id"], "type": "signal",
                              "label": signal["title"], "status": signal["severity"],
                              "confidence": signal["confidence"]}
            for source_id in signal["sourceIds"]:
                edges.append({"source": f"source:{source_id}", "target": node_id,
                              "type": "reports" if signal["kind"] == "coverage-gap" else "supports",
                              "confidence": signal["confidence"]})
            if signal.get("taskId") in task_ids:
                edges.append({"source": node_id, "target": f"task:{signal['taskId']}",
                              "type": "indicates", "confidence": signal["confidence"]})
        recommendation = intelligence["recommendation"]
        recommendation_id = f"recommendation:{recommendation['id']}"
        nodes[recommendation_id] = {"id": recommendation_id, "entityId": recommendation["id"],
                                    "type": "recommendation", "label": recommendation["title"],
                                    "status": recommendation["decision"], "confidence": recommendation["confidence"]}
        for signal_id in recommendation["signalIds"]:
            edges.append({"source": f"signal:{signal_id}", "target": recommendation_id,
                          "type": "motivates", "confidence": recommendation["confidence"]})
        if recommendation.get("taskId") in task_ids:
            edges.append({"source": recommendation_id, "target": f"task:{recommendation['taskId']}",
                          "type": "prioritizes", "confidence": recommendation["confidence"]})
    bounded_nodes, bounded_edges, bounds = _bounded_graph(nodes, edges, recommendation_id, limit)
    return {"nodes": bounded_nodes, "edges": bounded_edges,
            "legend": ["source", "signal", "recommendation", "theme", "task", "session", "blocker", "validation", "commit"],
            "bounds": bounds, "contractVersion": 1}


def temporal_map(project: Path, period: str = "week", anchor: str | None = None,
                 time_zone: str | None = None, limit: int = 160) -> dict[str, Any]:
    """Build a bounded graph only from evidence observed inside the requested local period."""
    if period == "all":
        zone, timezone_name = _review_zone(project, time_zone)
        with connect(project) as connection:
            lifecycle = [dict(row) for row in connection.execute(
                "SELECT task_events.id,task_events.task_id AS taskId,tasks.title AS taskTitle,"
                "task_events.event_type AS type,task_events.summary,task_events.occurred_at AS occurredAt "
                "FROM task_events JOIN tasks ON tasks.id=task_events.task_id WHERE task_events.project_hash=? "
                "ORDER BY task_events.occurred_at DESC LIMIT 500", (root_hash(project),),
            )]
            observed = [dict(row) for row in connection.execute(
                "SELECT id,kind AS type,outcome,summary,occurred_at AS occurredAt FROM observations "
                "WHERE project_hash=? ORDER BY occurred_at DESC LIMIT 500", (root_hash(project),),
            )]
        activity = lifecycle + [{**item, "taskId": None, "taskTitle": "Project evidence",
                                 "evidenceOnly": True} for item in observed]
        activity.sort(key=lambda item: item["occurredAt"], reverse=True)
        guidance = {"headline": "Review the project evidence history",
                    "nextAction": "Select a day or graph node to narrow the next decision.",
                    "caution": "All history is bounded; activity volume is not a productivity score."}
        period_start = min((item["occurredAt"] for item in activity), default=None)
        period_end = datetime.now(zone).isoformat()
        is_current = True
    else:
        insights = period_insights(project, period, anchor, time_zone)
        activity = insights["activity"]
        guidance = insights["progressGuidance"]
        period_start, period_end = insights["periodStart"], insights["periodEnd"]
        timezone_name, is_current = insights["timeZone"], insights["isCurrent"]
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    source_id, signal_id = "source:period-evidence", "signal:period-guidance"
    recommendation_id = "recommendation:period-action"
    nodes[source_id] = {"id": source_id, "type": "source", "label": "Recorded period evidence", "status": "connected"}
    nodes[signal_id] = {"id": signal_id, "type": "signal", "label": guidance["headline"], "status": "information"}
    nodes[recommendation_id] = {"id": recommendation_id, "type": "recommendation", "label": guidance["nextAction"], "status": "proposed"}
    edges.extend([{"source": source_id, "target": signal_id, "type": "supports", "confidence": 1.0},
                  {"source": signal_id, "target": recommendation_id, "type": "motivates", "confidence": 1.0}])
    first_task: str | None = None
    for item in activity[:limit]:
        task_id = item.get("taskId")
        task_node = f"task:{task_id}" if task_id else source_id
        if task_id:
            nodes.setdefault(task_node, {"id": task_node, "entityId": task_id, "type": "task",
                                          "label": item.get("taskTitle") or "Recorded task", "status": "",
                                          "updatedAt": item["occurredAt"]})
            first_task = first_task or task_node
        evidence_id = f"period:{item['id']}"
        event_type = str(item.get("type", ""))
        node_type = "validation" if event_type in {"validation_passed", "validation_failed", "validation-run"} else "blocker" if event_type == "task_blocked" else "session"
        status = item.get("outcome") or ("passed" if event_type == "validation_passed" else "failed" if event_type == "validation_failed" else "open" if node_type == "blocker" else "")
        nodes[evidence_id] = {"id": evidence_id, "entityId": item["id"], "type": node_type,
                              "label": item["summary"], "status": status, "observedAt": item["occurredAt"]}
        edges.append({"source": evidence_id, "target": task_node,
                      "type": "validated_by" if node_type == "validation" else "blocked_by" if node_type == "blocker" else "worked_on", "confidence": 1.0})
    if first_task:
        edges.append({"source": recommendation_id, "target": first_task, "type": "prioritizes", "confidence": 1.0})
    bounded_nodes, bounded_edges, bounds = _bounded_graph(nodes, edges, recommendation_id, limit)
    return {"period": period, "periodStart": period_start, "periodEnd": period_end,
            "timeZone": timezone_name, "isCurrent": is_current, "guidance": guidance,
            "nodes": bounded_nodes, "edges": bounded_edges, "bounds": bounds, "contractVersion": 2}


def evidence_calendar(project: Path, time_zone: str | None = None, days: int = 365) -> dict[str, Any]:
    zone, timezone_name = _review_zone(project, time_zone)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    with connect(project) as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT occurred_at AS occurredAt,'lifecycle' AS source,event_type AS kind,'' AS outcome FROM task_events "
            "WHERE project_hash=? AND occurred_at>=? UNION ALL "
            "SELECT occurred_at,'automatic',kind,outcome FROM observations WHERE project_hash=? AND occurred_at>=?",
            (root_hash(project), cutoff.isoformat(), root_hash(project), cutoff.isoformat()),
        )]
    today = datetime.now(zone).date()
    buckets: dict[str, dict[str, Any]] = {
        (today - timedelta(days=offset)).isoformat(): {
            "date": (today - timedelta(days=offset)).isoformat(),
            "evidence": 0, "outcomes": 0, "attention": 0, "automatic": 0,
        }
        for offset in range(days - 1, -1, -1)
    }
    for row in rows:
        day = datetime.fromisoformat(row["occurredAt"]).astimezone(zone).date().isoformat()
        bucket = buckets.setdefault(day, {"date": day, "evidence": 0, "outcomes": 0, "attention": 0, "automatic": 0})
        bucket["evidence"] += 1
        bucket["automatic"] += row["source"] == "automatic"
        bucket["outcomes"] += row["kind"] in {"task_completed", "validation_passed"} or row["outcome"] == "passed"
        bucket["attention"] += row["kind"] in {"task_blocked", "validation_failed"} or row["outcome"] == "failed"
    return {"timeZone": timezone_name, "days": sorted(buckets.values(), key=lambda item: item["date"]),
            "caution": "Evidence density shows recorded context, not productivity or effort."}


def agent_sessions(project: Path, limit: int = 12) -> list[dict[str, Any]]:
    with connect(project) as connection:
        return [dict(row) for row in connection.execute(
            "WITH latest AS (SELECT session_id,MAX(sequence) AS sequence FROM task_events "
            "WHERE project_hash=? AND session_id IS NOT NULL GROUP BY session_id) "
            "SELECT sessions.id,task_events.task_id AS taskId,sessions.agent_name AS agentName,"
            "sessions.model_name AS modelName,sessions.started_at AS startedAt,"
            "sessions.ended_at AS endedAt,tasks.title AS taskTitle FROM sessions "
            "JOIN latest ON latest.session_id=sessions.id "
            "JOIN task_events ON task_events.session_id=sessions.id AND task_events.sequence=latest.sequence "
            "LEFT JOIN tasks ON tasks.id=task_events.task_id WHERE sessions.project_hash=? "
            "ORDER BY task_events.occurred_at DESC LIMIT ?",
            (root_hash(project), root_hash(project), limit)
        )]


def weekly_review(project: Path, days: int = 7) -> dict[str, Any]:
    if not 1 <= days <= 365:
        raise ValueError("review days must be between 1 and 365")
    zone, timezone_name = _review_zone(project)
    local_end = datetime.now(zone)
    if days == 7:
        local_start = (local_end - timedelta(days=local_end.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:
        local_start = local_end - timedelta(days=days)
    start, end = local_start.astimezone(UTC), local_end.astimezone(UTC)
    review_id = f"review_{hashlib.sha256(f'{root_hash(project)}:{start.date()}:{end.date()}:{days}'.encode()).hexdigest()[:20]}"
    with connect(project) as connection:
        events = [dict(row) for row in connection.execute(
            "SELECT event_type,COUNT(*) AS count FROM task_events WHERE project_hash=? AND occurred_at>=? "
            "GROUP BY event_type ORDER BY event_type", (root_hash(project), start.isoformat())
        )]
        validation = [dict(row) for row in connection.execute(
            "SELECT validations.outcome,COUNT(*) AS count FROM validations JOIN tasks ON tasks.id=validations.task_id "
            "WHERE tasks.project_hash=? AND validations.occurred_at>=? GROUP BY validations.outcome",
            (root_hash(project), start.isoformat()),
        )]
        raw_daily = [dict(row) for row in connection.execute(
            "SELECT event_type,occurred_at FROM task_events WHERE project_hash=? AND occurred_at>=? ORDER BY occurred_at",
            (root_hash(project), start.isoformat()),
        )]
        completed_tasks = [dict(row) for row in connection.execute(
            "SELECT id,title,theme,completed_at AS completedAt FROM tasks WHERE project_hash=? "
            "AND completed_at>=? ORDER BY completed_at DESC LIMIT 50",
            (root_hash(project), start.isoformat()),
        )]
        active = connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_hash=? AND status IN ('active','blocked','needs_validation')",
            (root_hash(project),),
        ).fetchone()[0]
        effort = dict(connection.execute(
            "SELECT COALESCE(SUM(estimated_minutes),0) AS estimatedMinutes,"
            "COALESCE(SUM(actual_minutes),0) AS reportedActualMinutes FROM tasks "
            "WHERE project_hash=? AND updated_at>=?", (root_hash(project), start.isoformat())
        ).fetchone())
        observed = connection.execute(
            "SELECT COALESCE(SUM((julianday(ended_at)-julianday(started_at))*1440),0) FROM sessions "
            "WHERE project_hash=? AND ended_at IS NOT NULL AND ended_at>=?",
            (root_hash(project), start.isoformat()),
        ).fetchone()[0]
        effort["observedSessionMinutes"] = max(0, round(float(observed), 1))
        effort["evidence"] = ["task estimates/reported actuals", "ended AI session timestamps"]
        buckets: dict[str, dict[str, Any]] = {}
        cursor = local_start.date()
        while cursor <= local_end.date():
            key = cursor.isoformat()
            buckets[key] = {"date": key, "label": cursor.strftime("%a"), "started": 0,
                            "completed": 0, "blocked": 0, "validated": 0, "failed": 0}
            cursor += timedelta(days=1)
        event_fields = {"task_started": "started", "task_completed": "completed",
                        "task_blocked": "blocked", "validation_passed": "validated",
                        "validation_failed": "failed"}
        for item in raw_daily:
            local_date = datetime.fromisoformat(item["occurred_at"]).astimezone(zone).date().isoformat()
            field = event_fields.get(item["event_type"])
            if local_date in buckets and field:
                buckets[local_date][field] += 1
        result = {"id": review_id, "periodStart": start.isoformat(), "periodEnd": end.isoformat(),
                  "events": events, "validations": validation, "unfinished": active, "effort": effort,
                  "days": list(buckets.values()), "completedTasks": completed_tasks,
                  "timeZone": timezone_name, "evidence": ["structured task events", "validation outcomes"]}
        connection.execute(
            "INSERT INTO reviews(id,project_hash,period_start,period_end,facts_json,summary,created_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET period_end=excluded.period_end,"
            "facts_json=excluded.facts_json,summary=excluded.summary",
            (review_id, root_hash(project), start.isoformat(), end.isoformat(),
             json.dumps(result, ensure_ascii=False, separators=(",", ":")),
             f"{active} unfinished task(s); {len(validation)} validation outcome group(s).", end.isoformat()),
        )
    return result


def recommendations(project: Path) -> list[dict[str, Any]]:
    review = weekly_review(project)
    generated_at = datetime.now(UTC).isoformat()
    counts = {item["event_type"]: item["count"] for item in review["events"]}
    suggestions: list[dict[str, Any]] = []
    if review["unfinished"] > 2:
        suggestions.append({"title": "Finish or pause active work", "reason": "Several tasks remain open.", "generatedAt": generated_at,
                            "evidence": [f"{review['unfinished']} unfinished tasks"]})
    if counts.get("validation_failed", 0):
        suggestions.append({"title": "State proof and edge cases earlier", "reason": "Recent validation failed.", "generatedAt": generated_at,
                            "evidence": [f"{counts['validation_failed']} failed validation event(s)"]})
    if counts.get("task_blocked", 0):
        suggestions.append({"title": "Resolve the oldest blocker", "reason": "Blocked work is recorded.", "generatedAt": generated_at,
                            "evidence": [f"{counts['task_blocked']} blocker event(s)"]})
    if not suggestions:
        suggestions.append({"title": "Keep the next request small", "reason": "No strong risk signal is present.", "generatedAt": generated_at,
                            "evidence": ["No failed validation or blocker event this week"]})
    with connect(project) as connection:
        for suggestion in suggestions:
            idea_id = "idea_" + hashlib.sha256(
                f"{review['id']}:{suggestion['title']}".encode("utf-8")
            ).hexdigest()[:20]
            connection.execute(
                "INSERT OR IGNORE INTO ideas(id,project_hash,title,reason,evidence_json,status,created_at) "
                "VALUES(?,?,?,?,?,'proposed',?)",
                (idea_id, root_hash(project), suggestion["title"], suggestion["reason"],
                 json.dumps(suggestion["evidence"], ensure_ascii=False), generated_at),
            )
        rows = connection.execute(
            "SELECT id,task_id AS taskId,title,reason,evidence_json,status,created_at AS generatedAt "
            "FROM ideas WHERE project_hash=? ORDER BY created_at DESC LIMIT 50", (root_hash(project),)
        ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["evidence"] = json.loads(item.pop("evidence_json"))
    return result


def set_idea_status(project: Path, idea_id: str, status: str) -> dict[str, Any]:
    if status not in {"proposed", "accepted", "rejected", "planned", "completed"}:
        raise ValueError("unsupported idea status")
    with connect(project) as connection:
        cursor = connection.execute(
            "UPDATE ideas SET status=? WHERE id=? AND project_hash=?", (status, idea_id, root_hash(project))
        )
        if not cursor.rowcount:
            raise ValueError("idea not found")
        row = connection.execute(
            "SELECT id,task_id AS taskId,title,reason,evidence_json,status,created_at AS generatedAt "
            "FROM ideas WHERE id=?", (idea_id,)
        ).fetchone()
    result = dict(row)
    result["evidence"] = json.loads(result.pop("evidence_json"))
    return result


def safe_export(project: Path) -> dict[str, Any]:
    with connect(project) as connection:
        tasks = [dict(row) for row in connection.execute(
            "SELECT id,title,summary,theme,status,confidence,estimated_minutes,actual_minutes,created_at,updated_at,started_at,completed_at "
            "FROM tasks WHERE project_hash=? ORDER BY created_at", (root_hash(project),)
        )]
        events = [dict(row) for row in connection.execute(
            "SELECT id,task_id,event_type,summary,evidence_json,confidence,occurred_at FROM task_events "
            "WHERE project_hash=? ORDER BY sequence", (root_hash(project),)
        )]
        decisions = [dict(row) for row in connection.execute(
            "SELECT decisions.id,decisions.task_id,decisions.summary,decisions.reason,decisions.created_at "
            "FROM decisions JOIN tasks ON tasks.id=decisions.task_id WHERE tasks.project_hash=?", (root_hash(project),)
        )]
        blockers = [dict(row) for row in connection.execute(
            "SELECT blockers.id,blockers.task_id,blockers.summary,blockers.status,blockers.created_at,blockers.resolved_at "
            "FROM blockers JOIN tasks ON tasks.id=blockers.task_id WHERE tasks.project_hash=?", (root_hash(project),)
        )]
        validations = [dict(row) for row in connection.execute(
            "SELECT validations.id,validations.task_id,validations.category,validations.command_summary,"
            "validations.outcome,validations.duration_ms,validations.occurred_at FROM validations "
            "JOIN tasks ON tasks.id=validations.task_id WHERE tasks.project_hash=?", (root_hash(project),)
        )]
        observations = [dict(row) for row in connection.execute(
            "SELECT id,task_id,event_id,source,kind,scope,outcome,summary,metric_value,metric_unit,"
            "confidence,occurred_at,collected_at FROM observations WHERE project_hash=? "
            "ORDER BY occurred_at", (root_hash(project),)
        )]
        collector_receipts = [dict(row) for row in connection.execute(
            "SELECT id,collector,trigger_kind AS trigger,status,observed_count AS observedCount,"
            "accepted_count AS acceptedCount,duplicate_count AS duplicateCount,rejected_count AS rejectedCount,"
            "error_code AS errorCode,contract_version AS contractVersion,started_at AS startedAt,"
            "finished_at AS finishedAt FROM collector_receipts WHERE project_hash=? "
            "ORDER BY finished_at", (root_hash(project),)
        )]
        corrections = [dict(row) for row in connection.execute(
            "SELECT corrections.id,corrections.task_id,corrections.field_name,corrections.previous_value,"
            "corrections.corrected_value,corrections.reason,corrections.created_at FROM corrections "
            "LEFT JOIN tasks ON tasks.id=corrections.task_id WHERE tasks.project_hash=?", (root_hash(project),)
        )]
        ideas = [dict(row) for row in connection.execute(
            "SELECT id,task_id,title,reason,evidence_json,status,created_at FROM ideas WHERE project_hash=?",
            (root_hash(project),),
        )]
        reviews = [dict(row) for row in connection.execute(
            "SELECT id,period_start,period_end,facts_json,summary,created_at FROM reviews WHERE project_hash=?",
            (root_hash(project),),
        )]
        git_links = [dict(row) for row in connection.execute(
            "SELECT task_commits.task_id,task_commits.commit_hash,task_commits.confidence,task_commits.status,"
            "task_commits.evidence_json FROM task_commits JOIN tasks ON tasks.id=task_commits.task_id "
            "WHERE tasks.project_hash=?", (root_hash(project),)
        )]
        notifications = [dict(row) for row in connection.execute(
            "SELECT id,kind,severity,title,body,evidence_json,state,period_start AS periodStart,"
            "period_end AS periodEnd,created_at AS createdAt,updated_at AS updatedAt,delivered_at AS deliveredAt "
            "FROM notifications WHERE project_hash=? ORDER BY updated_at", (root_hash(project),),
        )]
    for event in events:
        event["evidence"] = json.loads(event.pop("evidence_json"))
    for item in ideas + git_links:
        item["evidence"] = json.loads(item.pop("evidence_json"))
    for item in notifications:
        item["evidence"] = json.loads(item.pop("evidence_json"))
    for review in reviews:
        review["facts"] = json.loads(review.pop("facts_json"))
    return {"format": "project-tasks-export-v2", "tasks": tasks, "events": events,
            "decisions": decisions, "blockers": blockers, "validations": validations,
            "observations": observations,
            "collectorReceipts": collector_receipts,
            "corrections": corrections, "ideas": ideas, "reviews": reviews, "gitLinks": git_links,
            "notifications": notifications,
            "excluded": ["raw prompts", "source contents", "raw repository command output",
                         "native agent session IDs"]}
