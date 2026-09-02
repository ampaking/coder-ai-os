from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coderai.project_tasks.analytics import (
    period_insights,
    recent_activity,
    task_evidence_summaries,
    task_graph,
)
from coderai.project_tasks.demo import seed_demo


class ProjectTasksDemoTest(unittest.TestCase):
    def test_demo_seed_exercises_dashboard_projections_without_real_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="project-tasks-demo-test-") as directory:
            project = Path(directory).resolve()
            seed_demo(project)

            tasks = task_evidence_summaries(project)
            self.assertEqual(len(tasks), 8)
            self.assertEqual({task["status"] for task in tasks}, {"active", "blocked", "completed"})
            self.assertGreaterEqual(len(recent_activity(project)), 8)

            graph = task_graph(project)
            self.assertTrue({"task", "theme", "session", "blocker", "validation"}.issubset(
                {node["type"] for node in graph["nodes"]}
            ))
            self.assertIn("depends_on", {edge["type"] for edge in graph["edges"]})

            for period in ("day", "week", "month", "year"):
                with self.subTest(period=period):
                    insights = period_insights(project, period)
                    self.assertEqual(insights["period"], period)
                    self.assertTrue(insights["buckets"])
                    self.assertEqual(
                        insights["totals"]["completed"],
                        sum(bucket["completed"] for bucket in insights["buckets"]),
                    )
                    self.assertEqual(
                        insights["totals"]["totalEvents"],
                        sum(len(bucket["eventIds"]) for bucket in insights["buckets"]),
                    )

            self.assertEqual(sum(task["status"] == "completed" for task in tasks), 6)
            self.assertGreaterEqual(period_insights(project, "year")["totals"]["completed"], 1)


if __name__ == "__main__":
    unittest.main()
