import frappe


def execute():
    """
    Slice 1 — Corrales Foundation

    Adds is_corral + corral_type custom fields to Warehouse so the app can
    distinguish physical corrales (animal locations) from inventory warehouses.
    Also adds 'Marca' to Animal Event event_type options.
    """

    # ── Warehouse custom fields ──────────────────────────────────────────────
    warehouse_fields = [
        {
            "dt": "Warehouse",
            "fieldname": "is_corral",
            "fieldtype": "Check",
            "label": "Es Corral",
            "insert_after": "warehouse_name",
            "default": "0",
            "description": "Marcar si este depósito es un corral físico ganadero",
        },
        {
            "dt": "Warehouse",
            "fieldname": "corral_type",
            "fieldtype": "Select",
            "label": "Tipo de Corral",
            "options": "\nAcostumbramiento\nSanidad\nOperativo",
            "insert_after": "is_corral",
            "depends_on": "eval:doc.is_corral == 1",
            "mandatory_depends_on": "eval:doc.is_corral == 1",
            "description": "Tipo funcional del corral: determina dieta y manejo operativo",
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
                "insert_after": cf_def.get("insert_after"),
                "options": cf_def.get("options"),
                "default": cf_def.get("default"),
                "depends_on": cf_def.get("depends_on"),
                "mandatory_depends_on": cf_def.get("mandatory_depends_on"),
                "description": cf_def.get("description"),
            })
            cf.insert()
            print(f"Created Custom Field: {cf_name}")
        else:
            print(f"Custom Field already exists: {cf_name}")

    # ── Animal Event — add Marca to event_type options ───────────────────────
    # The Animal Event doctype is a custom doctype, so we update its field
    # options directly via Property Setter to avoid touching the JSON file
    prop_setter_name = "Animal Event-event_type-options"
    existing_options = frappe.db.get_value(
        "Property Setter",
        {"doc_type": "Animal Event", "field_name": "event_type", "property": "options"},
        "value",
    )

    current_options = existing_options or "Pesada\nSanidad\nMovimiento\nMortandad\nCambio de Categoría\nOtro"

    if "Marca" not in current_options:
        new_options = current_options + "\nMarca"
        if frappe.db.exists(
            "Property Setter",
            {"doc_type": "Animal Event", "field_name": "event_type", "property": "options"},
        ):
            frappe.db.set_value(
                "Property Setter",
                {"doc_type": "Animal Event", "field_name": "event_type", "property": "options"},
                "value",
                new_options,
            )
            print("Updated Animal Event event_type options with Marca")
        else:
            ps = frappe.get_doc({
                "doctype": "Property Setter",
                "doctype_or_field": "DocField",
                "doc_type": "Animal Event",
                "field_name": "event_type",
                "property": "options",
                "value": new_options,
                "property_type": "Text",
            })
            ps.insert()
            print("Created Property Setter for Animal Event event_type options")
    else:
        print("Animal Event event_type already has Marca option")

    frappe.db.commit()
