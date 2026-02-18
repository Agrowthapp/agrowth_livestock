import frappe
from frappe.model.document import Document
from frappe import _


class AnimalEvent(Document):
    def validate(self):
        self.prepopulate_fields()
        self.validate_event_type()

    def prepopulate_fields(self):
        """Prepopulate read-only fields from animal"""
        if self.animal and not self.is_new():
            animal = frappe.get_doc("Animal", self.animal)
            if not self.weight:
                self.weight = animal.current_weight

    def validate_event_type(self):
        """Validate required fields for each event type"""
        if self.event_type == "Pesada":
            if not self.new_weight:
                frappe.throw(_("Para evento de 'Pesada' debe especificar el peso nuevo"))

        elif self.event_type == "Cambio de Categoría":
            if not self.new_category:
                frappe.throw(_("Para evento de 'Cambio de Categoría' debe especificar la nueva categoría"))

        elif self.event_type == "Movimiento":
            if not self.new_warehouse:
                frappe.throw(_("Para evento de 'Movimiento' debe especificar el nuevo depósito"))

    def on_submit(self):
        self.apply_event()

    def on_cancel(self):
        # For now, no automatic reversal on cancel
        # Could implement rollback if needed
        pass

    def apply_event(self):
        """Apply the event changes to the animal"""
        animal = frappe.get_doc("Animal", self.animal)

        if self.event_type == "Pesada" and self.new_weight:
            animal.current_weight = self.new_weight

        elif self.event_type == "Cambio de Categoría" and self.new_category:
            animal.current_category = self.new_category

        elif self.event_type == "Movimiento":
            if self.new_warehouse:
                animal.warehouse = self.new_warehouse
            if self.new_herd_batch:
                animal.current_herd_batch = self.new_herd_batch

        elif self.event_type == "Mortandad":
            # Mark animal as inactive or create a death record
            animal.current_category = "Muerto"
            # Could also create a stock movement for exit

        animal.save(ignore_permissions=True)

        frappe.msgprint(
            _("Evento aplicado: {0} para animal {1}").format(
                self.event_type, animal.ear_tag_id
            )
        )
