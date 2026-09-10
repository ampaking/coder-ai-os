"""Tasks 05–06 — understand the request: what else it implies, and how to divide it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coderai.evidence import split
from coderai.evidence.acceptance import Acceptance, Item, item_id
from coderai.evidence.companions import Companion, MIN_SCORE, infer, nearest_analogue

FEATURE_REPO = [
    "api/invoices.py", "web/InvoiceCard.tsx", "web/__tests__/InvoiceCard.test.tsx",
    "locales/en/invoices.json", "locales/ja/invoices.json", "tests/test_invoices.py",
    "api/subscriptions.py", "tests/test_subscriptions.py",
]
HEADLESS_REPO = ["worker/retry.py", "worker/queue.py", "tests/test_retry.py",
                 "worker/billing.py"]


def item(text: str) -> Item:
    return Item(id=item_id(text), text=text)


class NearestAnalogue(unittest.TestCase):
    def test_it_picks_the_structurally_closest_file(self) -> None:
        analogue, score = nearest_analogue("api/subscriptions.py",
                                           [p for p in FEATURE_REPO if p != "api/subscriptions.py"])
        self.assertEqual(analogue, "api/invoices.py")
        self.assertGreater(score, MIN_SCORE)

    def test_a_file_is_never_its_own_analogue(self) -> None:
        analogue, _ = nearest_analogue("api/invoices.py", FEATURE_REPO)
        self.assertNotEqual(analogue, "api/invoices.py")


class CompanionInference(unittest.TestCase):
    def infer(self, added, existing=FEATURE_REPO):
        return infer(Path("."), added, existing=existing)

    def test_a_new_endpoint_surfaces_the_ui_test_and_translations(self) -> None:
        found = self.infer(["api/subscriptions.py"])
        expected = {companion.expected for companion in found}
        self.assertIn("web/SubscriptionCard.tsx", expected)
        self.assertIn("web/__tests__/SubscriptionCard.test.tsx", expected)
        self.assertIn("locales/ja/subscriptions.json", expected)
        self.assertIn("locales/en/subscriptions.json", expected)

    def test_every_item_names_the_file_that_justifies_it(self) -> None:
        for companion in self.infer(["api/subscriptions.py"]):
            with self.subTest(expected=companion.expected):
                self.assertIn(companion.analogue, FEATURE_REPO)
                self.assertIn("because", companion.describe())

    def test_kinds_are_named_in_the_projects_own_terms(self) -> None:
        kinds = {c.expected: c.kind for c in self.infer(["api/subscriptions.py"])}
        self.assertEqual(kinds["web/SubscriptionCard.tsx"], "UI surface")
        self.assertEqual(kinds["locales/ja/subscriptions.json"], "translations")
        self.assertEqual(kinds["web/__tests__/SubscriptionCard.test.tsx"], "test")

    def test_work_already_present_is_not_demanded_again(self) -> None:
        found = self.infer(["api/subscriptions.py"])
        self.assertNotIn("tests/test_subscriptions.py",
                         {companion.expected for companion in found})

    def test_a_headless_service_infers_no_ui(self) -> None:
        found = self.infer(["worker/reconcile.py"], existing=HEADLESS_REPO)
        self.assertEqual([c for c in found if c.kind == "UI surface"], [])

    def test_no_credible_analogue_means_silence(self) -> None:
        self.assertEqual(self.infer(["totally/unrelated/thing.xyz"], existing=[]), [])

    def test_a_weak_analogy_is_marked_not_asserted(self) -> None:
        found = self.infer(["api/subscriptions.py"])
        for companion in found:
            self.assertGreaterEqual(companion.confidence, MIN_SCORE)
            if not companion.strong:
                self.assertIn("weak analogy", companion.describe())

    def test_it_executes_nothing_and_reads_bounded_input(self) -> None:
        from coderai.evidence.companions import MAX_COMPANIONS, MAX_FILES
        self.assertLessEqual(MAX_COMPANIONS, 20)
        self.assertLessEqual(MAX_FILES, 10_000)
        self.assertLessEqual(len(self.infer(["api/subscriptions.py"])), MAX_COMPANIONS)


class Splitting(unittest.TestCase):
    def test_a_single_surface_request_is_not_split(self) -> None:
        acceptance = Acceptance(items=[item("API: POST /subscriptions returns 201"),
                                       item("API: rejects an unknown plan code")])
        result = split.plan(acceptance, [])
        self.assertFalse(result.split)
        self.assertIn("single surface", result.reason)

    def test_api_plus_ui_splits_along_that_seam(self) -> None:
        acceptance = Acceptance(items=[
            item("API: POST /subscriptions returns 201"),
            item("UI: the plan card shows the renewal date"),
            item("UI: mobile 375px layout does not overflow"),
        ])
        result = split.plan(acceptance, [])
        self.assertTrue(result.split)
        self.assertEqual({child.surface for child in result.children}, {"api", "ui"})

    def test_inferred_companions_create_their_own_children(self) -> None:
        acceptance = Acceptance(items=[item("API: POST /subscriptions returns 201")])
        companions = [
            Companion(kind="UI surface", expected="web/SubscriptionCard.tsx",
                      analogue="web/InvoiceCard.tsx", source="api/subscriptions.py",
                      confidence=0.8),
            Companion(kind="translations", expected="locales/ja/subscriptions.json",
                      analogue="locales/ja/invoices.json", source="api/subscriptions.py",
                      confidence=0.8),
        ]
        result = split.plan(acceptance, companions)
        surfaces = {child.surface for child in result.children}
        self.assertEqual(surfaces, {"api", "ui", "i18n"})

    def test_children_are_ordered_by_dependency(self) -> None:
        acceptance = Acceptance(items=[
            item("UI: plan card shows the renewal date"),
            item("migration: add the subscriptions table"),
            item("API: POST /subscriptions returns 201"),
            item("i18n: ja and en strings for the card"),
        ])
        order = [child.surface for child in split.plan(acceptance, []).order()]
        self.assertEqual(order, ["schema", "api", "ui", "i18n"])

    def test_one_big_surface_is_reported_rather_than_forced(self) -> None:
        acceptance = Acceptance(items=[item(f"API: endpoint number {index} works")
                                       for index in range(9)])
        result = split.plan(acceptance, [])
        self.assertFalse(result.split)
        self.assertIn("consider splitting", result.reason)

    def test_nothing_to_plan_is_not_an_error(self) -> None:
        self.assertFalse(split.plan(None, []).split)
        self.assertEqual(split.plan(Acceptance(), []).children, [])

    def test_each_child_carries_its_own_work(self) -> None:
        acceptance = Acceptance(items=[
            item("API: POST /subscriptions returns 201"),
            item("UI: mobile 375px layout does not overflow"),
        ])
        children = {child.surface: child for child in split.plan(acceptance, []).children}
        self.assertEqual(len(children["api"].items), 1)
        self.assertEqual(len(children["ui"].items), 1)
        self.assertNotEqual(children["api"].items[0].id, children["ui"].items[0].id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
