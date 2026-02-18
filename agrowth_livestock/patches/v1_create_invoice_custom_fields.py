import frappe


def execute():
    custom_fields_def = [
        {"dt": "Purchase Invoice", "fieldname": "document_type", "fieldtype": "Select", "label": "Tipo de Documento", "options": "Factura\nLiquidación Hacienda\nLiquidación Grano\nSin Factura", "insert_after": "naming_series", "allow_on_submit": 1},
        {"dt": "Purchase Invoice", "fieldname": "livestock_section", "fieldtype": "Section Break", "label": "Datos Ganaderos", "insert_after": "document_type"},
        {"dt": "Purchase Invoice", "fieldname": "livestock_settlement", "fieldtype": "Link", "label": "Liquidación Ganadera", "options": "Livestock Settlement", "insert_after": "livestock_section", "read_only": 1, "allow_on_submit": 1},
        {"dt": "Sales Invoice", "fieldname": "document_type", "fieldtype": "Select", "label": "Tipo de Documento", "options": "Factura\nLiquidación Hacienda\nLiquidación Grano\nSin Factura", "insert_after": "naming_series", "allow_on_submit": 1},
        {"dt": "Sales Invoice", "fieldname": "livestock_section", "fieldtype": "Section Break", "label": "Datos Ganaderos", "insert_after": "document_type"},
        {"dt": "Sales Invoice", "fieldname": "livestock_dispatch", "fieldtype": "Link", "label": "Despacho Ganadero", "options": "Livestock Dispatch", "insert_after": "livestock_section", "read_only": 1, "allow_on_submit": 1},
        {"dt": "Item", "fieldname": "is_livestock_category", "fieldtype": "Check", "label": "Es Categoría Ganadera", "insert_after": "is_stock_item", "default": "0"}
    ]

    for cf_def in custom_fields_def:
        cf_name = frappe.db.exists("Custom Field", f"{cf_def['dt']}-{cf_def['fieldname']}")
        if not cf_name:
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
                "read_only": cf_def.get("read_only", 0),
                "allow_on_submit": cf_def.get("allow_on_submit", 0)
            })
            cf.insert()
            print(f"Created {cf_def['dt']}.{cf_def['fieldname']}")

    frappe.db.commit()
