"""Backfill LivestockStockLedgerEntry rows for confirmed Livestock Opening Balances
that were confirmed before the on_submit hook existed.

Safe to run multiple times — skips openings that already have ledger entries."""

import frappe
from frappe.utils import cint, now


def execute():
    openings = frappe.db.get_all(
        "Livestock Opening Balance",
        filters={"docstatus": 1},
        fields=["name", "company", "posting_date"],
    )

    if not openings:
        return

    created = 0
    skipped = 0

    for opening in openings:
        opening_name = opening["name"]

        existing = frappe.db.sql(
            "SELECT COUNT(*) FROM `tabLivestockStockLedgerEntry` WHERE voucher_type=%s AND voucher_no=%s",
            ("Livestock Opening Balance", opening_name),
        )[0][0]

        if existing > 0:
            skipped += 1
            continue

        items = frappe.db.get_all(
            "Livestock Opening Balance Item",
            filters={"parent": opening_name},
            fields=["idx", "category", "sex", "warehouse", "heads_qty", "total_weight_kg", "total_cost"],
        )

        for item in items:
            qty = cint(item.get("heads_qty") or 0)
            if qty <= 0:
                continue

            name = frappe.generate_hash(length=10)
            now_ts = now()

            frappe.db.sql(
                """
                INSERT INTO `tabLivestockStockLedgerEntry`
                (name, creation, modified, modified_by, owner, docstatus, idx,
                 company, posting_date, movement_type, category, sex, warehouse,
                 heads_qty, total_weight_kg, total_value, voucher_type, voucher_no, voucher_line_index)
                VALUES (%s, %s, %s, %s, %s, 0, 0, %s, %s, 'opening', %s, %s, %s, %s, %s, %s, 'Livestock Opening Balance', %s, %s)
                """,
                (
                    name, now_ts, now_ts, "Administrator", "Administrator",
                    opening["company"], opening["posting_date"],
                    item.get("category") or "",
                    item.get("sex") or "Sin especificar",
                    item.get("warehouse"),
                    qty,
                    item.get("total_weight_kg") or 0,
                    item.get("total_cost") or 0,
                    opening_name,
                    item.get("idx") or 0,
                ),
            )

        created += 1

    frappe.db.commit()
    print(f"Backfill v12: {created} openings processed, {skipped} skipped (already had entries).")
