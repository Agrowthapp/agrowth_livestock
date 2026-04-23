import frappe


def execute():
    """
    Slice 8 — Corral Field Link

    Adds agro_field custom field to Warehouse so corrales can be linked
    to their parent Agro Field (campo).
    """

    # ── Warehouse custom fields ──────────────────────────────────────────────
    warehouse_fields = [
        {
            "dt": "Warehouse",
            "fieldname": "agro_field",
            "fieldtype": "Link",
            "label": "Campo",
            "options": "Agro Field",
            "insert_after": "corral_type",
            "depends_on": "eval:doc.is_corral == 1",
            "description": "Campo al que pertenece este corral",
        },
    ]

    for cf_def in warehouse_fields:
        cf_name = f"{cf_def['dt']}-{cf_def['fieldname']}"
        if not frappe.db.exists("Custom Field", cf_name):
            cf = frappe.get_doc({
                "doctype": "Custom Field",
                "dt": cf_def["dt"],
                "module": "Livestock",
                "fieldname": cf_def["fieldname"],
                "fieldtype": cf_def["fieldtype"],
                "label": cf_def["label"],
                "options": cf_def.get("options"),
                "insert_after": cf_def.get("insert_after"),
                "depends_on": cf_def.get("depends_on"),
                "description": cf_def.get("description"),
            })
            cf.insert()
            print(f"Created Custom Field: {cf_name}")
        else:
            print(f"Custom Field already exists: {cf_name}")

    frappe.db.commit()
