import frappe
from frappe.model.document import Document
from frappe import _
from agrowth_livestock.utils import (
    calculate_withholdings,
    add_withholdings_to_invoice,
    get_iva_rate
)


class LivestockDispatch(Document):
    def validate(self):
        self.validate_mode()
        self.validate_items()
        self.calculate_totals()

    def validate_mode(self):
        if self.mode == "Full Batch":
            if not self.herd_batch:
                frappe.throw(_("Para modo 'Tropa Completa' debe seleccionar una tropa"))
        else:
            if self.herd_batch:
                frappe.throw(_("Para modo 'Mixto' no debe seleccionar tropa, agregue líneas manualmente"))
            if not self.items:
                frappe.throw(_("Para modo 'Mixto' debe agregar al menos una línea"))

    def validate_items(self):
        if not self.items:
            frappe.throw(_("El despacho debe tener al menos una línea"))

        for line in self.items:
            if not line.item_code:
                frappe.throw(_("Cada línea debe tener un artículo"))

            if line.qty_heads <= 0:
                frappe.throw(_("La cantidad de cabezas debe ser mayor a 0"))

            # Auto-fill tax rate from item if not set
            if not line.tax_rate:
                line.tax_rate = get_iva_rate(line.item_code)
            if not line.tax_amount:
                line.tax_amount = (line.amount or 0) * (line.tax_rate or 0) / 100

            # Validate stock availability
            self.validate_stock(line)

    def validate_stock(self, line):
        """Validate that there's enough stock in the herd batch"""
        if not line.herd_batch:
            return

        batch = frappe.get_doc("Herd Batch", line.herd_batch)
        available_qty = 0

        for batch_line in batch.lines:
            if batch_line.item_code == line.item_code:
                available_qty = batch_line.qty_heads
                break

        if line.qty_heads > available_qty:
            frappe.throw(
                _("Stock insuficiente en tropa {0}: disponible {1}, solicitado {2}").format(
                    line.herd_batch, available_qty, line.qty_heads
                )
            )

    def calculate_totals(self):
        total_bruto = 0
        total_iva = 0

        for line in self.items:
            total_bruto += line.amount or 0
            total_iva += line.tax_amount or 0

        self.total_bruto = total_bruto
        self.total_iva = total_iva

        # Calculate withholdings if profile exists
        withholdings = calculate_withholdings(self, total_bruto, "Customer")
        total_retenciones = sum(w["amount"] for w in withholdings)
        
        self.total_retenciones = total_retenciones

        # Net = Gross + VAT - Withholdings
        self.total_neto = total_bruto + total_iva - total_retenciones

    def on_submit(self):
        if self.mode == "Full Batch":
            self.populate_from_batch()

        self.create_sales_invoice()
        self.create_stock_entry()
        self.update_herd_batch()

    def on_cancel(self):
        self.cancel_sales_invoice()
        self.cancel_stock_entry()
        self.restore_herd_batch()

    def populate_from_batch(self):
        """Populate lines from selected herd batch (Full Batch mode)"""
        if not self.herd_batch:
            return

        # Clear existing items
        self.items = []

        batch = frappe.get_doc("Herd Batch", self.herd_batch)

        for batch_line in batch.lines:
            # Get VAT rate from item
            tax_rate = self.get_iva_rate(batch_line.item_code)

            dispatch_line = self.append("items", {
                "herd_batch": self.herd_batch,
                "item_code": batch_line.item_code,
                "category": batch_line.category,
                "qty_heads": batch_line.qty_heads,
                "avg_weight": batch_line.avg_weight,
                "unit_price": batch_line.unit_price,
                "amount": batch_line.amount,
                "tax_rate": tax_rate,
                "tax_amount": (batch_line.amount or 0) * (tax_rate / 100)
            })

    def get_iva_rate(self, item_code):
        """Get IVA rate from item"""
        from agrowth_livestock.utils import get_iva_rate_from_item
        return get_iva_rate_from_item(item_code)

    def create_sales_invoice(self):
        """Create a Sales Invoice in draft state"""
        if self.sales_invoice:
            frappe.throw(_("Ya existe una Factura de Venta asociada"))

        si = frappe.new_doc("Sales Invoice")
        si.company = self.company
        si.customer = self.customer
        si.posting_date = self.posting_date

        # Add item lines
        for line in self.items:
            si_item = si.append("items")
            si_item.item_code = line.item_code
            si_item.qty = line.qty_heads
            si_item.rate = line.unit_price
            si_item.amount = line.amount
            si_item.warehouse = self.warehouse

        # Add VAT as taxes
        if self.total_iva > 0:
            vat_account = frappe.db.get_value("Company", self.company, "default_vat_output_account")
            if vat_account:
                si.append("taxes", {
                    "charge_type": "On Net Total",
                    "account_head": vat_account,
                    "rate": 0,
                    "tax_amount": self.total_iva,
                    "description": "IVA"
                })

        # Add withholdings
        if self.withholding_profile and self.total_retenciones > 0:
            withholdings = calculate_withholdings(self, self.total_bruto, "Customer")
            add_withholdings_to_invoice(si, withholdings, is_purchase=False)

        si.insert(ignore_permissions=True)

        # Update reference
        self.sales_invoice = si.name
        self.save(ignore_permissions=True)

        frappe.msgprint(_("Factura de Venta {0} creada en estado draft").format(si.name))

    def create_stock_entry(self):
        """Create a Stock Entry of type Material Issue"""
        if self.stock_entry:
            frappe.throw(_("Ya existe una Salida de Stock asociada"))

        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Material Issue"
        se.purpose = "Material Issue"
        se.company = self.company
        se.posting_date = self.posting_date

        for line in self.items:
            se_item = se.append("items")
            se_item.item_code = line.item_code
            se_item.qty = line.qty_heads
            se_item.s_warehouse = self.warehouse
            se_item.conversion_factor = 1

        se.insert(ignore_permissions=True)

        # Update reference
        self.stock_entry = se.name
        self.save(ignore_permissions=True)

        frappe.msgprint(_("Salida de Stock {0} creada en estado draft").format(se.name))

    def update_herd_batch(self):
        """Update herd batch status based on sale type"""
        if self.mode == "Full Batch" and self.herd_batch:
            # Complete sale - close the batch
            batch = frappe.get_doc("Herd Batch", self.herd_batch)
            batch.status = "Sold"
            batch.save(ignore_permissions=True)
        else:
            # Partial sale - deduct quantities from batches
            self.deduct_from_batches()

    def deduct_from_batches(self):
        """Deduct quantities from multiple herd batches (Mixed mode)"""
        batch_quantities = {}

        # Group by batch
        for line in self.items:
            if line.herd_batch not in batch_quantities:
                batch_quantities[line.herd_batch] = {}
            if line.item_code not in batch_quantities[line.herd_batch]:
                batch_quantities[line.herd_batch][line.item_code] = 0
            batch_quantities[line.herd_batch][line.item_code] += line.qty_heads

        # Update each batch
        for batch_name, items in batch_quantities.items():
            batch = frappe.get_doc("Herd Batch", batch_name)

            for batch_line in batch.lines:
                if batch_line.item_code in items:
                    batch_line.qty_heads -= items[batch_line.item_code]

                    # If quantity reaches 0, mark batch as sold
                    if batch_line.qty_heads <= 0:
                        batch.status = "Sold"

            batch.save(ignore_permissions=True)

    def cancel_sales_invoice(self):
        """Cancel the Sales Invoice"""
        if self.sales_invoice:
            si = frappe.get_doc("Sales Invoice", self.sales_invoice)
            if si.docstatus == 1:
                # If submitted, create credit note
                si.cancel()
            elif si.docstatus == 0:
                # If draft, delete
                frappe.delete_doc("Sales Invoice", si.name)

            self.sales_invoice = None
            self.save(ignore_permissions=True)

    def cancel_stock_entry(self):
        """Cancel the Stock Entry"""
        if self.stock_entry:
            se = frappe.get_doc("Stock Entry", self.stock_entry)
            if se.docstatus == 1:
                # If submitted, create reverse entry
                se.cancel()
            elif se.docstatus == 0:
                frappe.delete_doc("Stock Entry", se.name)

            self.stock_entry = None
            self.save(ignore_permissions=True)

    def restore_herd_batch(self):
        """Restore herd batch to previous state"""
        if self.mode == "Full Batch" and self.herd_batch:
            # Restore full batch to active
            batch = frappe.get_doc("Herd Batch", self.herd_batch)
            batch.status = "Active"
            batch.save(ignore_permissions=True)
        else:
            # Restore quantities to batches
            self.restore_batch_quantities()

    def restore_batch_quantities(self):
        """Restore quantities to herd batches"""
        batch_quantities = {}

        for line in self.items:
            if line.herd_batch not in batch_quantities:
                batch_quantities[line.herd_batch] = {}
            if line.item_code not in batch_quantities[line.herd_batch]:
                batch_quantities[line.herd_batch][line.item_code] = 0
            batch_quantities[line.herd_batch][line.item_code] += line.qty_heads

        for batch_name, items in batch_quantities.items():
            batch = frappe.get_doc("Herd Batch", batch_name)

            for batch_line in batch.lines:
                if batch_line.item_code in items:
                    batch_line.qty_heads += items[batch_line.item_code]

            # Restore to active if it was sold
            if batch.status == "Sold":
                batch.status = "Active"

            batch.save(ignore_permissions=True)
