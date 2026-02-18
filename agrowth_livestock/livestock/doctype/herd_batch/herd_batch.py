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

        self.total_heads = total_heads
        self.total_weight = total_weight
        self.total_amount = total_amount
