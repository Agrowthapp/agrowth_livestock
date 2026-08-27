"""
Tests for the v9 backfill patch idempotency (livestock-entry-settlement-boundary PR1).

Covers task 1.4 + 1.5: the patch MUST be safe to re-run on a bench that already
executed it once, on a bench with no legacy settlements, and on a bench with
partially-applied prior runs. It MUST NOT create duplicate intakes, MUST NOT
move Herd Batches, and MUST NOT touch Stock Entry. It MUST skip settlements
that already have a linked intake.

The patch is split into two layers:

  * `agrowth_livestock.backfill_legacy_settlement_policy` — pure idempotency
    policy. Frappe-free, importable in CI without a bench.
  * `agrowth_livestock.patches.v9_backfill_pending_intake_for_legacy_settlements.execute()`
    — the Frappe patch entry point (idempotent at the DB level by checking
    existing intake links before inserting). Bench-side smoke test only.

The pure policy is what we unit-test here. The Frappe `execute()` is bench-side
smoke-tested (out of band) and relies on the policy for its decisions.
"""

import unittest

from agrowth_livestock.backfill_legacy_settlement_policy import (
    is_legacy_submitted_settlement,
    should_backfill_legacy_settlement,
)


def _submitted_settlement(name="LQS-2026-0001", docstatus=1, herd_batch="HB-001", stock_entry="SE-001"):
    return {
        "name": name,
        "docstatus": docstatus,
        "herd_batch": herd_batch,
        "stock_entry": stock_entry,
    }


class IsLegacySubmittedSettlementTests(unittest.TestCase):
    def test_submitted_settlement_with_stock_artifacts_is_legacy(self):
        self.assertTrue(is_legacy_submitted_settlement(_submitted_settlement()))

    def test_draft_settlement_is_not_legacy(self):
        self.assertFalse(is_legacy_submitted_settlement(_submitted_settlement(docstatus=0)))

    def test_cancelled_settlement_is_not_legacy(self):
        self.assertFalse(is_legacy_submitted_settlement(_submitted_settlement(docstatus=2)))


class ShouldBackfillLegacySettlementTests(unittest.TestCase):
    def test_submitted_settlement_without_intake_link_is_backfilled(self):
        settlement = _submitted_settlement()
        existing_intake_names = set()
        self.assertTrue(
            should_backfill_legacy_settlement(settlement, existing_intake_names),
            "A submitted settlement with no linked intake MUST be backfilled",
        )

    def test_submitted_settlement_with_existing_intake_is_skipped(self):
        settlement = _submitted_settlement()
        # The intake name is derived from the settlement name (LI-... or
        # the same source-name pattern). The patch uses an exact name match.
        existing_intake_names = {settlement["name"]}
        self.assertFalse(
            should_backfill_legacy_settlement(settlement, existing_intake_names),
            "A submitted settlement that already has a linked intake MUST be skipped",
        )

    def test_draft_settlement_is_skipped(self):
        settlement = _submitted_settlement(docstatus=0)
        self.assertFalse(
            should_backfill_legacy_settlement(settlement, set()),
            "A draft settlement MUST NOT be backfilled — it is not a legacy artifact",
        )

    def test_cancelled_settlement_is_skipped(self):
        settlement = _submitted_settlement(docstatus=2)
        self.assertFalse(
            should_backfill_legacy_settlement(settlement, set()),
            "A cancelled settlement MUST NOT be backfilled",
        )

    def test_idempotent_rerun_when_all_intakes_already_exist(self):
        # Simulates a second execution of `bench execute` after the first
        # run already inserted all the intakes.
        settlements = [
            _submitted_settlement(name=f"LQS-2026-{i:04d}") for i in range(1, 6)
        ]
        existing_intake_names = {s["name"] for s in settlements}
        backfilled = [
            s["name"]
            for s in settlements
            if should_backfill_legacy_settlement(s, existing_intake_names)
        ]
        self.assertEqual(
            backfilled,
            [],
            "Re-running the patch on a fully migrated bench MUST be a no-op",
        )

    def test_partial_legacy_set_backfills_only_missing(self):
        settlements = [_submitted_settlement(name=f"LQS-2026-{i:04d}") for i in range(1, 4)]
        # Only the second settlement already has a linked intake.
        existing_intake_names = {settlements[1]["name"]}
        backfilled = [
            s["name"]
            for s in settlements
            if should_backfill_legacy_settlement(s, existing_intake_names)
        ]
        self.assertEqual(
            backfilled,
            [settlements[0]["name"], settlements[2]["name"]],
            "Patch must backfill exactly the settlements with no linked intake",
        )

    def test_decision_does_not_mutate_inputs(self):
        settlement = _submitted_settlement()
        existing = set()
        before_settlement = dict(settlement)
        before_existing = set(existing)
        should_backfill_legacy_settlement(settlement, existing)
        self.assertEqual(settlement, before_settlement)
        self.assertEqual(existing, before_existing)

    def test_triangulation_stress_mixed_states_and_partial_intakes(self):
        # Triangulation: across a realistic mix of submitted/draft/cancelled
        # settlements and partial intake coverage, the policy produces exactly
        # the missing-link set, no more, no less. Catches hardcoded Fake-It
        # implementations that return True for everything or that mutate the
        # input set.
        settlements = []
        for i in range(1, 21):
            if i % 3 == 0:
                docstatus = 0  # draft
            elif i % 5 == 0:
                docstatus = 2  # cancelled
            else:
                docstatus = 1  # submitted
            settlements.append(_submitted_settlement(
                name=f"LQS-2026-{i:04d}",
                docstatus=docstatus,
            ))

        # Half of the submitted settlements already have an intake link.
        submitted_indices = [
            i for i, s in enumerate(settlements) if s["docstatus"] == 1
        ]
        pre_existing = {
            settlements[i]["name"] for i in submitted_indices[::2]
        }
        before = set(pre_existing)
        backfilled = {
            s["name"]
            for s in settlements
            if should_backfill_legacy_settlement(s, pre_existing)
        }
        # The input set must not be mutated.
        self.assertEqual(pre_existing, before)
        # Exactly the submitted settlements without an existing link.
        expected = {
            settlements[i]["name"]
            for i in submitted_indices
            if settlements[i]["name"] not in pre_existing
        }
        self.assertEqual(backfilled, expected)


if __name__ == "__main__":
    unittest.main()
