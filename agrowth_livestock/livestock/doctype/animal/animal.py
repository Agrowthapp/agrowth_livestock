import frappe
from frappe.model.document import Document
from frappe import _


class Animal(Document):
    def validate(self):
        self.validate_ear_tag()
        self.validate_warehouse()

    def validate_ear_tag(self):
        """Validate that ear_tag_id is unique"""
        if self.ear_tag_id:
            existing = frappe.db.exists("Animal", {
                "ear_tag_id": self.ear_tag_id,
                "name": ["!=", self.name]
            })
            if existing:
                frappe.throw(_("El número de caravana {0} ya está en uso").format(self.ear_tag_id))

    def validate_warehouse(self):
        """Validate warehouse belongs to company"""
        if self.warehouse and self.current_herd_batch:
            batch = frappe.get_doc("Herd Batch", self.current_herd_batch)
            if batch.warehouse != self.warehouse:
                frappe.throw(_("El depósito del animal debe coincidir con el depósito de la tropa"))

    def on_submit(self):
        self.create_serial_no()

    def on_cancel(self):
        self.cancel_serial_no()

    def create_serial_no(self):
        """Create a Serial No in ERPNext for this animal"""
        if self.serial_no:
            frappe.throw(_("Ya existe un Número de Serie asociado"))

        # Determine item code based on category
        item_code = self.get_item_code_from_category()

        # Create Serial No
        serial_no = frappe.new_doc("Serial No")
        serial_no.item_code = item_code
        serial_no.serial_no = self.ear_tag_id
        serial_no.warehouse = self.warehouse
        serial_no.purchase_date = self.birth_date or frappe.utils.today()

        serial_no.insert(ignore_permissions=True)

        # Update reference
        self.serial_no = serial_no.name
        self.save(ignore_permissions=True)

        frappe.msgprint(_("Número de Serie {0} creado").format(serial_no.name))

    def cancel_serial_no(self):
        """Cancel the Serial No"""
        if self.serial_no:
            serial = frappe.get_doc("Serial No", self.serial_no)
            if serial.docstatus == 1:
                serial.cancel()
            elif serial.docstatus == 0:
                frappe.delete_doc("Serial No", serial.name)

            self.serial_no = None
            self.save(ignore_permissions=True)

    def get_item_code_from_category(self):
        """Get the Item code based on animal category"""
        category_item_map = {
            "Ternero": "Bovino - Ternero",
            "Novillo": "Bovino - Novillo",
            "Vaquillona": "Bovino - Vaquillona",
            "Vaca": "Bovino - Vaca",
            "Toro": "Bovino - Toro"
        }

        item_code = category_item_map.get(self.current_category)
        if not item_code:
            # Check if item exists
            if frappe.db.exists("Item", item_code):
                return item_code
            else:
                frappe.throw(_("No se encontró un artículo para la categoría {0}").format(self.current_category))

        return item_code

    def update_location(self, new_warehouse, new_batch=None):
        """Update animal location"""
        self.warehouse = new_warehouse
        if new_batch:
            self.current_herd_batch = new_batch
        self.save(ignore_permissions=True)

        # Update Serial No warehouse
        if self.serial_no:
            serial = frappe.get_doc("Serial No", self.serial_no)
            serial.warehouse = new_warehouse
            serial.save(ignore_permissions=True)

    def update_category(self, new_category):
        """Update animal category"""
        old_category = self.current_category
        self.current_category = new_category
        self.save(ignore_permissions=True)

        # Update Serial No item_code
        if self.serial_no:
            serial = frappe.get_doc("Serial No", self.serial_no)
            new_item = self.get_item_code_from_category()
            if serial.item_code != new_item:
                serial.item_code = new_item
                serial.save(ignore_permissions=True)

        frappe.msgprint(
            _("Animal {0} cambió de {1} a {2}").format(
                self.ear_tag_id, old_category, new_category
            )
        )
