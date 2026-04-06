import frappe
from frappe.model.document import Document
from frappe import _


class LivestockOpeningBalance(Document):
    def validate(self):
        self._validate_no_duplicate_session()
        self._calculate_totals()

    def _validate_no_duplicate_session(self):
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
