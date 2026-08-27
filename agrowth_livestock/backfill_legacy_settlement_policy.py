"""
Pure policy for the v9 legacy-settlement backfill (livestock-entry-settlement-boundary PR1).

This module is deliberately Frappe-free so the backfill rules can be unit-tested
without a bench. The Frappe-bound `execute()` lives in
`agrowth_livestock.patches.v9_backfill_pending_intake_for_legacy_settlements`
and imports these helpers.
"""

SUBMITTED_DOCSTATUS = 1


def is_legacy_submitted_settlement(settlement_row):
    """
    A settlement is "legacy" for the purposes of this backfill iff it was
    submitted (docstatus == 1). Draft and cancelled settlements are out of scope
    because they are not operational artifacts.
    """
    return int(settlement_row.get("docstatus") or 0) == SUBMITTED_DOCSTATUS


def should_backfill_legacy_settlement(settlement_row, existing_intake_names):
    """
    Decide whether the patch must create a pending intake for a settlement.

    Rules:
      * Only submitted settlements are eligible (drafts / cancels are not
        operational artifacts).
      * If a settlement already has a linked intake, skip. The intake name
        contract is the settlement name itself (the intake has a `settlement`
        link to the settlement name), so a same-name match is the canonical
        idempotency check.
    """
    if not is_legacy_submitted_settlement(settlement_row):
        return False

    settlement_name = settlement_row.get("name")
    if not settlement_name:
        return False

    if settlement_name in existing_intake_names:
        return False

    return True
