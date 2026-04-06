import frappe
from frappe.model.document import Document
from frappe import _


class MigrationSession(Document):
    def validate(self):
        self._validate_cutoff_date_immutable()
        self._validate_no_active_session()

    def _validate_cutoff_date_immutable(self):
        if not self.is_new() and self.has_value_changed("cutoff_date"):
            frappe.throw(_("La fecha de corte no puede modificarse una vez creada la sesión"))

    def _validate_no_active_session(self):
        if not self.is_new():
            return

        active_statuses = [
            "Draft", "Config Complete", "Uploaded",
            "Validated", "Validation Errors", "Partially Posted",
        ]
        existing = frappe.db.exists(
            "Migration Session",
            {
                "company": self.company,
                "status": ["in", active_statuses],
                "name": ["!=", self.name],
            },
        )
        if existing:
            frappe.throw(
                _("Ya existe una sesión activa ({0}) para la empresa {1}").format(
                    existing, self.company
                )
            )
