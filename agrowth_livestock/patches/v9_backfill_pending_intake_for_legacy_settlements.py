"""
Slice 9 — Backfill Pending Intake for Legacy Settlements

The livestock-entry-settlement-boundary change moves physical materialization
out of `Livestock Settlement.on_submit()` and into `Livestock Intake.confirm_intake()`.
A migration patch is required so submitted settlements created BEFORE the
boundary change still receive a pending `Livestock Intake` they can confirm
through the new operational flow.

This patch is:

  * Idempotent — re-running MUST NOT create duplicate intakes. The decision
    is delegated to a pure policy function (`should_backfill_legacy_settlement`)
    in `agrowth_livestock.backfill_legacy_settlement_policy` so the rule is
    unit-testable without a bench.
  * Non-destructive — it does NOT create or move `Herd Batch` / `Stock Entry`.
    Legacy `herd_batch` / `stock_entry` data on the settlement stays in place
    but is no longer read by the BFF (PR2 onwards).
  * Marked — each new intake carries `migration_origin = "legacy"` so PR3 can
    surface them with a clear badge in the reconciliation tab.

The patch is invoked from `patches.txt` so `bench migrate` runs it once.
"""

import frappe

from agrowth_livestock.backfill_legacy_settlement_policy import (
    SUBMITTED_DOCSTATUS,
    is_legacy_submitted_settlement,
    should_backfill_legacy_settlement,
)


# Re-export the policy for any out-of-band bench smoke test that imports the
# patch module directly. The unit tests import the policy module instead.
__all__ = [
    "execute",
    "is_legacy_submitted_settlement",
    "should_backfill_legacy_settlement",
]


# ---------------------------------------------------------------------------
# Frappe patch entry point — runs at `bench migrate`
# ---------------------------------------------------------------------------

MIGRATION_ORIGIN = "legacy"
INTAKE_DOCTYPE = "Livestock Intake"
SETTLEMENT_DOCTYPE = "Livestock Settlement"

# Custom field for the migration marker. Created idempotently on first run.
MIGRATION_ORIGIN_FIELD = {
    "dt": INTAKE_DOCTYPE,
    "fieldname": "migration_origin",
    "fieldtype": "Data",
    "label": "Origen de migración",
    "description": "Marca la fila como creada por un patch de migración (ej. legacy).",
    "read_only": 1,
    "allow_on_submit": 0,
}


def _ensure_migration_origin_field():
    """Create the migration_origin custom field on Livestock Intake (idempotent)."""
    cf_name = f"{MIGRATION_ORIGIN_FIELD['dt']}-{MIGRATION_ORIGIN_FIELD['fieldname']}"
    if frappe.db.exists("Custom Field", cf_name):
        return

    cf = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": MIGRATION_ORIGIN_FIELD["dt"],
        "module": "Livestock",
        "fieldname": MIGRATION_ORIGIN_FIELD["fieldname"],
        "fieldtype": MIGRATION_ORIGIN_FIELD["fieldtype"],
        "label": MIGRATION_ORIGIN_FIELD["label"],
        "description": MIGRATION_ORIGIN_FIELD["description"],
        "read_only": MIGRATION_ORIGIN_FIELD["read_only"],
        "allow_on_submit": MIGRATION_ORIGIN_FIELD["allow_on_submit"],
    })
    cf.insert()
    frappe.db.commit()


def _existing_intake_settlement_names():
    """Return the set of settlement names that already have a linked intake."""
    rows = frappe.get_all(
        INTAKE_DOCTYPE,
        filters={"settlement": ["is", "set"]},
        fields=["settlement"],
        limit_page_length=0,
    )
    return {row["settlement"] for row in rows if row.get("settlement")}


def _submitted_settlements():
    """Return submitted settlement rows relevant to the backfill."""
    return frappe.get_all(
        SETTLEMENT_DOCTYPE,
        filters={"docstatus": SUBMITTED_DOCSTATUS},
        fields=["name", "docstatus", "company", "warehouse", "posting_date",
                "herd_batch", "stock_entry"],
        limit_page_length=0,
    )


def execute():
    """
    Run the v9 backfill.

    Steps:
      1. Ensure the `migration_origin` custom field exists on Livestock Intake.
      2. Collect the set of settlement names that already have a linked intake.
      3. Iterate submitted settlements. For each, ask the pure policy
         `should_backfill_legacy_settlement` whether to backfill.
      4. Create one pending intake per eligible settlement with
         `migration_origin = "legacy"`. The intake does NOT create or move
         Herd Batch / Stock Entry.
    """
    _ensure_migration_origin_field()

    existing = _existing_intake_settlement_names()
    submitted_rows = _submitted_settlements()
    created = 0
    skipped_existing = 0
    skipped_draft_or_cancel = 0

    for row in submitted_rows:
        if not should_backfill_legacy_settlement(row, existing):
            if not is_legacy_submitted_settlement(row):
                skipped_draft_or_cancel += 1
            else:
                skipped_existing += 1
            continue

        intake = frappe.new_doc(INTAKE_DOCTYPE)
        intake.company = row.get("company")
        intake.settlement = row["name"]
        # The settlement-side herd_batch / stock_entry fields are left as-is on
        # the settlement (legacy data) but the intake does NOT adopt them,
        # because the new boundary makes intake the sole owner of those
        # artifacts. PR2 will redirect reads to the intake DTO.
        intake.warehouse = row.get("warehouse")
        intake.posting_date = row.get("posting_date")
        intake.status = "Pendiente de ingreso"
        intake.confirmation_mode = "None"
        intake.expected_heads = 0  # legacy settlements did not materialize animals
        intake.received_heads = 0
        intake.missing_heads = 0
        intake.surplus_heads = 0
        intake.problem_heads = 0
        intake.has_discrepancy = 0
        intake.migration_origin = MIGRATION_ORIGIN
        intake.notes = (
            f"Backfill automático v9 desde liquidación {row['name']} "
            f"(migración legacy)."
        )
        intake.insert(ignore_permissions=True)

        # PR2 boundary: persist the reverse link so the settlement
        # row carries the canonical join key (intake) to the
        # operational track. The v9 backfill must mirror the
        # settlement-time `create_livestock_intake` behavior so the
        # BFF can resolve the join consistently across legacy and
        # post-PR2 data.
        try:
            frappe.db.set_value(
                SETTLEMENT_DOCTYPE,
                row["name"],
                "intake",
                intake.name,
                update_modified=False,
            )
        except Exception:
            # Defensive: the `intake` Link field may not exist on the
            # settlement doctype in environments that have not yet
            # picked up the F.1 review-fix field addition. The backfill
            # still succeeds; the BFF falls back to `intake.settlement`.
            frappe.logger().warning(
                f"[v9_backfill_pending_intake_for_legacy_settlements] "
                f"Could not set settlement.intake reverse link on {row['name']}. "
                f"BFF will fall back to intake.settlement join (PR2 review fix F.4)."
            )

        created += 1

    frappe.db.commit()

    print(
        f"[v9_backfill_pending_intake_for_legacy_settlements] "
        f"created={created} skipped_existing={skipped_existing} "
        f"skipped_draft_or_cancel={skipped_draft_or_cancel}"
    )
