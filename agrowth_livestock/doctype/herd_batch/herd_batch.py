import frappe
from frappe.model.document import Document


class HerdBatch(Document):
    def validate(self):
        self.validate_lines()
        self.calculate_totals()

    def validate_lines(self):
        if not self.lines:
            frappe.throw("La tropa debe tener al menos una línea")

        for line in self.lines:
            if not line.item_code:
                frappe.throw("Cada línea debe tener un artículo")

            if line.qty_heads <= 0:
                frappe.throw("La cantidad de cabezas debe ser mayor a 0")

    def calculate_totals(self):
        total_heads = 0
        total_weight = 0
        total_amount = 0

        for line in self.lines:
            total_heads += line.qty_heads or 0
            total_weight += line.total_weight or 0
            total_amount += line.amount or 0

        # Defensive: legacy Herd Batch schemas (pre-v10 patch) may not
        # declare `total_heads` / `total_weight` / `total_amount`. Setting
        # a non-existent attribute on a Document raises AttributeError
        # which Frappe turns into HTTP 417. Use `meta.get_field` to
        # decide whether to write.
        meta = frappe.get_meta(self.doctype)
        if meta.get_field("total_heads"):
            self.total_heads = total_heads
        if meta.get_field("total_weight"):
            self.total_weight = total_weight
        if meta.get_field("total_amount"):
            self.total_amount = total_amount
