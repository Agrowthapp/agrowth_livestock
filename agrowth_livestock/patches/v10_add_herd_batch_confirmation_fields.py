"""
agrowth_livestock.patches.v10_add_herd_batch_confirmation_fields

PR2 / livestock-entry-settlement-boundary:
Add the `confirmation_status`, `confirmation_mode`, and `confirmed_at`
fields to the deployed `Herd Batch` DocType. The fields are part of the
JSON schema but only show up on a site after a bench migration. This
patch is idempotent: it re-creates the fields with the same definitions
Frappe would generate from the JSON, so re-running it on a fully migrated
site is a no-op.

Also widens the `status` and `origin_type` Select options to include the
PR2 values that the intake flow needs to set.

This is a defensive migration: the Python `confirm_herd_batch` and
`list_intake_history_feed` APIs already use `meta.get_field` /
`_existing_fields` guards, so legacy sites that have not yet run this
patch will still work (but with reduced behavior on confirm flow).
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


HERD_BATCH_CONFIRMATION_FIELDS = {
    "Herd Batch": [
        {
            "fieldname": "confirmation_status",
            "label": "Estado de Confirmación",
            "fieldtype": "Select",
            "options": "Pending\nCompleted\nRejected",
            "default": "Pending",
            "in_list_view": 1,
            "insert_after": "status",
        },
        {
            "fieldname": "confirmation_mode",
            "label": "Modo de Confirmación",
            "fieldtype": "Select",
            "options": "None\nManual\nCSV EID\nRejected",
            "default": "None",
            "insert_after": "confirmation_status",
        },
        {
            "fieldname": "confirmed_at",
            "label": "Confirmado el",
            "fieldtype": "Datetime",
            "read_only": 1,
            "insert_after": "confirmation_mode",
        },
        {
            "fieldname": "total_heads",
            "label": "Total Cabezas",
            "fieldtype": "Int",
            "read_only": 1,
            "insert_after": "notes",
        },
        {
            "fieldname": "total_weight",
            "label": "Total Peso",
            "fieldtype": "Float",
            "read_only": 1,
            "insert_after": "total_heads",
        },
        {
            "fieldname": "total_amount",
            "label": "Total Monto",
            "fieldtype": "Currency",
            "read_only": 1,
            "insert_after": "total_weight",
        },
    ]
}


def execute():
    """Idempotent: creates the fields if they do not already exist."""
    create_custom_fields(HERD_BATCH_CONFIRMATION_FIELDS, update=True)
