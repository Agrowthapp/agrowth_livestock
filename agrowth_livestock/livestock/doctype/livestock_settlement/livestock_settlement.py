import frappe
from frappe.model.document import Document
from frappe import _
from agrowth_livestock.utils import (
    calculate_withholdings,
    add_withholdings_to_invoice,
    get_company_default_account,
    get_iva_rate,
)


class LivestockSettlement(Document):
    def validate(self):
        self.validate_items()
        self.calculate_totals()

    def validate_items(self):
        if not self.items:
            frappe.throw(_("La liquidación debe tener al menos una línea"))

        for line in self.items:
            if not line.item_code:
                frappe.throw(_("Cada línea debe tener un artículo"))
            
            if line.qty_heads <= 0:
                frappe.throw(_("La cantidad de cabezas debe ser mayor a 0"))

            if line.price_mode == "Por Kg" and not line.avg_weight:
                frappe.throw(_("Si el precio es por kg, debe especificar el peso promedio"))

            # Auto-fill tax rate from item if not set
            if not line.tax_rate:
                line.tax_rate = get_iva_rate(line.item_code)
            if not line.tax_amount:
                line.tax_amount = (line.amount or 0) * (line.tax_rate or 0) / 100

    def calculate_totals(self):
        total_bruto = 0
        total_iva = 0

        for line in self.items:
            total_bruto += line.amount or 0
            total_iva += line.tax_amount or 0

        self.total_bruto = total_bruto
        self.total_iva = total_iva
        
        # Calculate withholdings if profile exists
        withholdings = calculate_withholdings(self, total_bruto, "Supplier")
        total_retenciones = sum(w["amount"] for w in withholdings)
        
        self.total_retenciones = total_retenciones

        # Net = Gross + VAT - Withholdings + Commissions
        self.total_neto = (total_bruto + total_iva - 
                          total_retenciones + (self.total_comisiones or 0))

    def on_submit(self):
        self.create_purchase_invoice()
        self.create_herd_batch()
        self.create_stock_entry()
        self.create_livestock_intake()  # NEW: create pending intake

    def on_cancel(self):
        self.cancel_purchase_invoice()
        self.cancel_stock_entry()
        self.cancel_herd_batch()

    def create_purchase_invoice(self):
        """Crea una Purchase Invoice en estado draft"""
        if self.purchase_invoice:
            frappe.throw(_("Ya existe una Factura de Compra asociada"))

        pi = frappe.new_doc("Purchase Invoice")
        pi.company = self.company
        pi.supplier = self.supplier
        pi.posting_date = self.posting_date
        pi.bill_no = self.document_number
        if self.point_of_sale:
            pi.naming_series = f"PI-{self.point_of_sale}-"

        # Agregar líneas deitems
        for line in self.items:
            pi_item = pi.append("items")
            pi_item.item_code = line.item_code
            pi_item.qty = line.qty_heads
            pi_item.rate = line.unit_price
            pi_item.amount = line.amount
            pi_item.warehouse = self.warehouse

        # Agregar IVA como taxes
        if self.total_iva > 0:
            # Buscar cuenta de IVA por defecto
            iva_account = get_company_default_account(self.company, "default_vat_input_account")
            if iva_account:
                pi.append("taxes", {
                    "charge_type": "On Net Total",
                    "account_head": iva_account,
                    "rate": 0,  # Se calcula por línea
                    "tax_amount": self.total_iva,
                    "description": "IVA"
                })

        # Add withholdings
        if self.tax_profile and self.total_retenciones > 0:
            withholdings = calculate_withholdings(self, self.total_bruto, "Supplier")
            add_withholdings_to_invoice(pi, withholdings, is_purchase=True)

        pi.insert(ignore_permissions=True)
        
        # Actualizar referencia
        self.db_set("purchase_invoice", pi.name, update_modified=False)

        frappe.msgprint(_("Factura de Compra {0} creada en estado draft").format(pi.name))

    def create_herd_batch(self):
        """Crea un Herd Batch (Tropa)"""
        if self.herd_batch:
            frappe.throw(_("Ya existe una Tropa asociada"))

        batch = frappe.new_doc("Herd Batch")
        batch.company = self.company
        batch.warehouse = self.warehouse
        batch.origin_type = "Livestock Settlement"
        batch.origin_document = self.name
        batch.arrival_date = self.posting_date
        batch.status = "Pending Entry"
        batch.confirmation_status = "Pending"
        batch.confirmation_mode = "None"
        batch.notes = f"Liquidación: {self.name}"

        for line in self.items:
            batch_line = batch.append("lines")
            batch_line.species = line.species or "Bovino"
            batch_line.category = line.category or "Otro"
            batch_line.item_code = line.item_code
            batch_line.qty_heads = line.qty_heads
            batch_line.avg_weight = line.avg_weight
            batch_line.total_weight = line.total_weight
            batch_line.unit_price = line.unit_price
            batch_line.amount = line.amount

        batch.insert(ignore_permissions=True)

        # Actualizar referencia
        self.db_set("herd_batch", batch.name, update_modified=False)

        frappe.msgprint(_("Tropa {0} creada").format(batch.name))

    def create_stock_entry(self):
        """Crea un Stock Entry de tipo Material Receipt"""
        if self.stock_entry:
            frappe.throw(_("Ya existe una Entrada de Stock asociada"))

        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Material Receipt"
        se.purpose = "Material Receipt"
        se.company = self.company
        se.posting_date = self.posting_date

        for line in self.items:
            se_item = se.append("items")
            se_item.item_code = line.item_code
            se_item.qty = line.qty_heads
            se_item.t_warehouse = self.warehouse
            se_item.conversion_factor = 1

        se.insert(ignore_permissions=True)

        # Actualizar referencia
        self.db_set("stock_entry", se.name, update_modified=False)

        frappe.msgprint(_("Entrada de Stock {0} creada en estado draft").format(se.name))

    def cancel_purchase_invoice(self):
        """Cancela la Factura de Compra"""
        if self.purchase_invoice:
            pi = frappe.get_doc("Purchase Invoice", self.purchase_invoice)
            if pi.docstatus == 1:
                # Si está submitteada, crear nota de crédito
                pi.cancel()
            elif pi.docstatus == 0:
                # Si está draft, eliminar
                frappe.delete_doc("Purchase Invoice", pi.name)
            
            self.db_set("purchase_invoice", None, update_modified=False)

    def cancel_stock_entry(self):
        """Cancela la Entrada de Stock"""
        if self.stock_entry:
            se = frappe.get_doc("Stock Entry", self.stock_entry)
            if se.docstatus == 1:
                # Si está submitteada, crear salida inversa
                se.cancel()
            elif se.docstatus == 0:
                frappe.delete_doc("Stock Entry", se.name)
            
            self.db_set("stock_entry", None, update_modified=False)

    def cancel_herd_batch(self):
        """Cancela/eliminap Herd Batch"""
        if self.herd_batch:
            batch = frappe.get_doc("Herd Batch", self.herd_batch)
            # Marcamos como cancelado
            batch.status = "Closed"
            batch.save(ignore_permissions=True)
            
            self.db_set("herd_batch", None, update_modified=False)
    
    def create_livestock_intake(self):
        """
        Creates a pending Livestock Intake when settlement is submitted.
        This is the NEW operational layer between commercial expectation and physical receipt.
        """
        # Check if doctype exists (backward compatibility)
        if not frappe.db.exists("DocType", "Livestock Intake"):
            frappe.log_error("Livestock Intake doctype not found. Skipping intake creation.")
            return
        
        intake = frappe.new_doc("Livestock Intake")
        intake.company = self.company
        intake.settlement = self.name
        intake.herd_batch = self.herd_batch
        intake.warehouse = self.warehouse
        intake.posting_date = self.posting_date
        intake.status = "Pendiente de ingreso"
        intake.confirmation_mode = "None"
        
        # Calculate expected heads from settlement lines
        expected_heads = sum(line.qty_heads for line in self.items)
        intake.expected_heads = expected_heads
        intake.received_heads = 0
        intake.missing_heads = 0
        intake.surplus_heads = 0
        intake.problem_heads = 0
        intake.has_discrepancy = False
        
        # Create intake lines from settlement lines
        for line in self.items:
            intake_line = intake.append("lines")
            intake_line.item_code = line.item_code
            intake_line.species = line.species or "Bovino"
            intake_line.category = line.category or "Otro"
            intake_line.expected_heads = line.qty_heads
            intake_line.received_heads = 0
            intake_line.missing_heads = 0
            intake_line.surplus_heads = 0
        
        intake.notes = f"Generado automáticamente desde liquidación {self.name}"
        
        intake.insert(ignore_permissions=True)
        
        frappe.msgprint(_("Ingreso pendiente {0} creado").format(intake.name))
