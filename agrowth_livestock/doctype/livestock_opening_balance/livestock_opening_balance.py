import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import cint


class LivestockOpeningBalance(Document):
    def validate(self):
        self._validate_no_duplicate_session()
        self._calculate_totals()

    def on_submit(self):
        """Create LivestockStockLedgerEntry rows for each opening item so
        get_summary() can count them."""
        for item in self.items:
            if cint(item.heads_qty or 0) <= 0:
                continue
            frappe.get_doc({
                "doctype": "Livestock Stock Ledger Entry",
                "company": self.company,
                "posting_date": self.posting_date,
                "movement_type": "opening",
                "category": item.category or "",
                "sex": item.sex or "",
                "warehouse": item.get("warehouse"),
                "heads_qty": item.heads_qty,
                "total_weight_kg": item.total_weight_kg or 0,
                "total_value": item.total_cost or 0,
                "voucher_type": "Livestock Opening Balance",
                "voucher_no": self.name,
                "voucher_line_index": item.idx,
            }).insert()
        frappe.db.commit()

    def on_cancel(self):
        """Remove LivestockStockLedgerEntry rows associated with this opening."""
        frappe.db.delete(
            "Livestock Stock Ledger Entry",
            {
                "voucher_type": "Livestock Opening Balance",
                "voucher_no": self.name,
            },
        )
        frappe.db.commit()

    def _validate_no_duplicate_session(self):
        # Skip validation for in-app openings that don't have a migration session.
        if not self.migration_session:
            return
        existing = frappe.db.exists(
            "Livestock Opening Balance",
            {
                "migration_session": self.migration_session,
                "docstatus": ["!=", 2],
                "name": ["!=", self.name],
            },
        )
        if existing:
            frappe.throw(
                _("Ya existe un Livestock Opening Balance ({0}) para esta sesión").format(existing)
            )

    def _calculate_totals(self):
        total_heads = 0
        total_weight_kg = 0.0
        total_value = 0.0

        for item in self.items:
            total_heads += item.heads_qty or 0
            total_weight_kg += item.total_weight_kg or 0
            total_value += item.total_cost or 0

        self.total_heads = total_heads
        self.total_weight_kg = total_weight_kg
        self.total_value = total_value
