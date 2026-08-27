"""
agrowth_livestock.patches.v11_add_livestock_settlement_intake_field

Ensure deployed sites expose the reverse link from Livestock Settlement to
Livestock Intake. Some local sites can have the JSON definition present on
disk while the DocField row is missing after migrate; creating it as a custom
field keeps the BFF join contract stable and is idempotent.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


LIVESTOCK_SETTLEMENT_INTAKE_FIELD = {
    "Livestock Settlement": [
        {
            "fieldname": "intake",
            "label": "Livestock Intake",
            "fieldtype": "Link",
            "options": "Livestock Intake",
            "read_only": 1,
            "insert_after": "stock_entry",
        }
    ]
}


def execute():
    """Idempotent: creates or updates the settlement intake reverse link."""
    create_custom_fields(LIVESTOCK_SETTLEMENT_INTAKE_FIELD, update=True)
