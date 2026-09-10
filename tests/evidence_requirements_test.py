"""Tasks 03–04 — what must be proved comes from the diff, not from the claim."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from coderai.evidence import acceptance, requirements


def a_project(root: Path) -> Path:
    project = root / "app"
    (project / ".coder-ai" / "val").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
    return project


def with_val(project: Path, globs: list[str], app: str | None = None) -> None:
    target = (project / ".coder-ai" / "val" / "config.json" if app is None
              else project / ".coder-ai" / "val" / "apps" / app / "config.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"watchGlobs": globs}))


class DerivedRequirements(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = a_project(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_ui_change_demands_a_visual_run(self) -> None:
        with_val(self.project, ["web/**/*.tsx"])
        found = requirements.require(self.project, ["web/PlanCard.tsx", "api/plans.py"])
        visual = [item for item in found if item.kind == requirements.VISUAL]
        self.assertEqual(len(visual), 1)
        self.assertIn("PlanCard.tsx", visual[0].why)
        self.assertIn("val/run", visual[0].how)
        self.assertEqual(visual[0].paths, ["web/PlanCard.tsx"])

    def test_a_non_ui_change_demands_no_visual_run(self) -> None:
        with_val(self.project, ["web/**/*.tsx"])
        found = requirements.require(self.project, ["api/plans.py"])
        self.assertEqual([item for item in found if item.kind == requirements.VISUAL], [])

    def test_a_monorepo_app_is_named_in_the_command(self) -> None:
        with_val(self.project, ["apps/admin/**/*.tsx"], app="apps-admin")
        found = requirements.require(self.project, ["apps/admin/Page.tsx"])
        visual = [item for item in found if item.kind == requirements.VISUAL][0]
        self.assertIn("--app apps-admin", visual.how)

    def test_source_changes_demand_the_projects_own_test_command(self) -> None:
        (self.project / "Makefile").write_text("test:\n\tpytest -q\n")
        found = requirements.require(self.project, ["api/plans.py"])
        tests = [item for item in found if item.kind == requirements.TESTS][0]
        self.assertEqual(tests.how, "make test")
        self.assertTrue(tests.blocking)

    def test_an_undiscoverable_command_is_admitted_not_invented(self) -> None:
        found = requirements.require(self.project, ["api/plans.py"])
        tests = [item for item in found if item.kind == requirements.TESTS][0]
        self.assertEqual(tests.how, requirements.UNKNOWN)
        self.assertFalse(tests.blocking, "an unknown command must not block delivery")
        self.assertFalse(tests.known)

    def test_a_schema_change_is_flagged(self) -> None:
        found = requirements.require(self.project, ["alembic/versions/abc_add_table.py"])
        self.assertTrue([item for item in found if item.kind == requirements.CONTRACT])

    def test_requirements_are_stable_for_the_same_diff(self) -> None:
        with_val(self.project, ["web/**/*.tsx"])
        first = requirements.require(self.project, ["web/A.tsx"])
        second = requirements.require(self.project, ["web/A.tsx"])
        self.assertEqual([item.id for item in first], [item.id for item in second])

    def test_no_changes_means_no_requirements(self) -> None:
        self.assertEqual(requirements.require(self.project, []), [])

    def test_changed_files_sees_untracked_work(self) -> None:
        (self.project / "new.py").write_text("x = 1\n")
        self.assertIn("new.py", requirements.changed_files(self.project))

    def test_derivation_executes_nothing_it_finds(self) -> None:
        (self.project / "Makefile").write_text("test:\n\trm -rf /tmp/should-not-run\n")
        requirements.require(self.project, ["api/plans.py"])   # must not run the target


class AcceptanceList(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = a_project(Path(self._tmp.name))
        self.task = "subscriptions"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, body: str) -> acceptance.Acceptance:
        path = acceptance.path_for(self.project, self.task)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        loaded = acceptance.load(self.project, self.task)
        assert loaded is not None
        return loaded

    def test_items_parse_with_stable_ids(self) -> None:
        found = self.write("""# Acceptance

- [ ] API: POST /subscriptions returns 201
- [x] UI: plan card shows the renewal date
""")
        self.assertEqual(len(found), 2)
        self.assertFalse(found.items[0].checked)
        self.assertTrue(found.items[1].checked)
        self.assertEqual(found.items[0].id, acceptance.item_id(
            "API: POST /subscriptions returns 201"))

    def test_vague_items_are_flagged(self) -> None:
        found = self.write("- [ ] make the UI better\n- [ ] mobile 375px: card does not overflow\n")
        self.assertTrue(found.items[0].vague)
        self.assertFalse(found.items[1].vague)

    def test_a_missing_list_is_not_an_error(self) -> None:
        self.assertIsNone(acceptance.load(self.project, "nothing-here"))

    def test_a_removed_item_is_reported(self) -> None:
        first = self.write("- [ ] build the API\n- [ ] build the mobile layout\n")
        acceptance.record_start(self.project, self.task, first)
        second = self.write("- [x] build the API\n")
        drift = acceptance.drift_since_start(self.project, self.task, second)
        self.assertEqual(drift.removed, ["build the mobile layout"])
        self.assertTrue(drift.shrank)

    def test_a_typo_fix_is_not_reported_as_shrinkage(self) -> None:
        first = self.write("- [ ] mobile 375px: the card does not oveflow\n")
        acceptance.record_start(self.project, self.task, first)
        second = self.write("- [ ] mobile 375px: the card does not overflow\n")
        drift = acceptance.drift_since_start(self.project, self.task, second)
        self.assertEqual(drift.removed, [])

    def test_a_material_rewrite_is_reported(self) -> None:
        first = self.write("- [ ] mobile 375px: the plan card does not overflow\n")
        acceptance.record_start(self.project, self.task, first)
        second = self.write("- [ ] mobile: the plan card looks acceptable somehow\n")
        drift = acceptance.drift_since_start(self.project, self.task, second)
        self.assertTrue(drift.shrank)

    def test_added_items_are_accepted_silently(self) -> None:
        first = self.write("- [ ] build the API\n")
        acceptance.record_start(self.project, self.task, first)
        second = self.write("- [ ] build the API\n- [ ] and the ja translations\n")
        drift = acceptance.drift_since_start(self.project, self.task, second)
        self.assertFalse(drift.shrank)
        self.assertEqual(drift.added, ["and the ja translations"])

    def test_a_list_written_after_the_work_is_flagged(self) -> None:
        found = self.write("- [ ] build the API\n")
        acceptance.record_start(self.project, self.task, found, first_edit_at=100.0, now=200.0)
        drift = acceptance.drift_since_start(self.project, self.task, found)
        self.assertTrue(drift.retrospective)

    def test_no_recorded_start_means_no_drift_claim(self) -> None:
        found = self.write("- [ ] build the API\n")
        self.assertFalse(acceptance.drift_since_start(self.project, self.task, found).shrank)

    def test_the_fingerprint_ignores_order_and_spacing(self) -> None:
        first = self.write("- [ ] alpha item here\n- [ ] beta item here\n")
        second = self.write("- [ ]   beta   item here\n- [ ] alpha item here\n")
        self.assertEqual(first.fingerprint(), second.fingerprint())


if __name__ == "__main__":
    unittest.main(verbosity=2)
