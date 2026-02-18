import frappe
from frappe.model.document import Document
from frappe import _


class LivestockReclassification(Document):
    def validate(self):
        self.validate_items()
        self.validate_stock()

    def validate_items(self):
        if self.from_item == self.to_item:
            frappe.throw(_("El artículo origen debe ser diferente al artículo destino"))

        if self.qty <= 0:
            frappe.throw(_("La cantidad debe ser mayor a 0"))

    def validate_stock(self):
        """Validate that there's enough stock in the herd batch"""
        if not self.herd_batch:
            return

        batch = frappe.get_doc("Herd Batch", self.herd_batch)
        available_qty = 0
        warehouse = batch.warehouse

        for batch_line in batch.lines:
            if batch_line.item_code == self.from_item:
                available_qty = batch_line.qty_heads
                break

        if self.qty > available_qty:
            frappe.throw(
                _("Stock insuficiente en tropa {0}: disponible {1}, solicitado {2}").format(
                    self.herd_batch, available_qty, self.qty
                )
            )

    def on_submit(self):
        self.create_stock_entry()
        self.update_herd_batch()

    def on_cancel(self):
        self.cancel_stock_entry()
        self.restore_herd_batch()

    def create_stock_entry(self):
        """Create a Stock Entry of type Repack (material transfer)"""
        if self.stock_entry:
            frappe.throw(_("Ya existe un Movimiento de Stock asociado"))

        # Get the warehouse from the herd batch
        batch = frappe.get_doc("Herd Batch", self.herd_batch)
        warehouse = batch.warehouse

        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Repack"
        se.purpose = "Repack"
        se.company = self.company
        se.posting_date = self.posting_date
        se.from_warehouse = warehouse
        se.to_warehouse = warehouse

        # Issue line (from_item)
        issue_item = se.append("items")
        issue_item.item_code = self.from_item
        issue_item.qty = self.qty
        issue_item.s_warehouse = warehouse
        issue_item.transfer_qty = self.qty

        # Receipt line (to_item)
        receipt_item = se.append("items")
        receipt_item.item_code = self.to_item
        receipt_item.qty = self.qty
        receipt_item.t_warehouse = warehouse
        receipt_item.transfer_qty = self.qty

        se.insert(ignore_permissions=True)

        # Update reference
        self.stock_entry = se.name
        self.save(ignore_permissions=True)

        frappe.msgprint(_("Movimiento de Stock {0} creado en estado draft").format(se.name))

    def update_herd_batch(self):
        """Update herd batch lines: deduct from_item, add to_item"""
        batch = frappe.get_doc("Herd Batch", self.herd_batch)

        # Find and deduct from_item
        from_line_found = False
        for batch_line in batch.lines:
            if batch_line.item_code == self.from_item:
                batch_line.qty_heads -= self.qty
                from_line_found = True

                # If quantity reaches 0, remove the line or keep with 0
                if batch_line.qty_heads <= 0:
                    # Keep it but mark it as 0
                    pass

                break

        if not from_line_found:
            frappe.throw(_("No se encontró el artículo origen en la tropa"))

        # Find and add to to_item
        to_line_found = False
        for batch_line in batch.lines:
            if batch_line.item_code == self.to_item:
                batch_line.qty_heads += self.qty
                to_line_found = True
                break

        # If to_item doesn't exist in batch, add new line
        if not to_line_found:
            # Get category from to_item
            to_item_doc = frappe.get_doc("Item", self.to_item)

            new_line = batch.append("lines")
            new_line.item_code = self.to_item
            new_line.qty_heads = self.qty
            new_line.species = "Bovino"  # Could be derived from item
            # Try to infer category from item code
            new_line.category = self.infer_category_from_item(to_item_doc.name)

        batch.save(ignore_permissions=True)

        frappe.msgprint(_("Tropa {0} actualizada").format(self.herd_batch))

    def infer_category_from_item(self, item_code):
        """Infer category from item code"""
        item_code_upper = item_code.upper()

        if "TERNERO" in item_code_upper:
            return "Ternero"
        elif "NOVILLO" in item_code_upper:
            return "Novillo"
        elif "VAQUILLONA" in item_code_upper:
            return "Vaquillona"
        elif "VACA" in item_code_upper:
            return "Vaca"
        elif "TORO" in item_code_upper:
            return "Toro"
        else:
            return "Otro"

    def cancel_stock_entry(self):
        """Cancel the Stock Entry"""
        if self.stock_entry:
            se = frappe.get_doc("Stock Entry", self.stock_entry)
            if se.docstatus == 1:
                se.cancel()
            elif se.docstatus == 0:
                frappe.delete_doc("Stock Entry", se.name)

            self.stock_entry = None
            self.save(ignore_permissions=True)

    def restore_herd_batch(self):
        """Restore herd batch to previous state"""
        batch = frappe.get_doc("Herd Batch", self.herd_batch)

        # Restore from_item quantity
        for batch_line in batch.lines:
            if batch_line.item_code == self.from_item:
                batch_line.qty_heads += self.qty
                break

        # Deduct to_item quantity
        for batch_line in batch.lines:
            if batch_line.item_code == self.to_item:
                batch_line.qty_heads -= self.qty

                # If quantity reaches 0, remove the line
                if batch_line.qty_heads <= 0:
                    batch.remove(batch_line)
                break

        batch.save(ignore_permissions=True)

        frappe.msgprint(_("Tropa {0} restaurada").format(self.herd_batch))
