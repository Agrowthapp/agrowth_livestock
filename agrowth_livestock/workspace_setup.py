import frappe
import json


def _workspace_payload():
    return {
        "doctype": "Workspace",
        "label": "Ganadería",
        "title": "Ganadería",
        "module": "Livestock",
        "app": "agrowth_livestock",
        "public": 1,
        "is_hidden": 0,
        "icon": "fa fa-leanpub",
        "content": json.dumps([
            {"id": "header_ganado", "type": "header", "data": {"text": "<span class=\"h4\"><b>Ganado</b></span>", "col": 12}},
            {"id": "card_ganado", "type": "card", "data": {"card_name": "Ganado", "col": 4}},
            {"id": "card_operaciones", "type": "card", "data": {"card_name": "Operaciones", "col": 4}},
            {"id": "card_configuracion", "type": "card", "data": {"card_name": "Configuración", "col": 4}}
        ]),
        "links": [
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Ganado",
                "link_count": 4,
                "onboard": 0,
                "type": "Card Break"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Animal",
                "link_count": 0,
                "link_to": "Animal",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Herd Batch",
                "link_count": 0,
                "link_to": "Herd Batch",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Herd Batch Line",
                "link_count": 0,
                "link_to": "Herd Batch Line",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Livestock Reclassification",
                "link_count": 0,
                "link_to": "Livestock Reclassification",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Operaciones",
                "link_count": 5,
                "onboard": 0,
                "type": "Card Break"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Animal Event",
                "link_count": 0,
                "link_to": "Animal Event",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Livestock Settlement",
                "link_count": 0,
                "link_to": "Livestock Settlement",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Livestock Settlement Line",
                "link_count": 0,
                "link_to": "Livestock Settlement Line",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Livestock Dispatch",
                "link_count": 0,
                "link_to": "Livestock Dispatch",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Livestock Dispatch Line",
                "link_count": 0,
                "link_to": "Livestock Dispatch Line",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Configuración",
                "link_count": 2,
                "onboard": 0,
                "type": "Card Break"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Withholding Profile",
                "link_count": 0,
                "link_to": "Withholding Profile",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            },
            {
                "hidden": 0,
                "is_query_report": 0,
                "label": "Withholding Rule",
                "link_count": 0,
                "link_to": "Withholding Rule",
                "link_type": "DocType",
                "onboard": 0,
                "type": "Link"
            }
        ]
    }


def ensure_workspaces():
    workspace_name = "Ganadería"
    if frappe.db.exists("Workspace", workspace_name):
        return

    workspace = frappe.get_doc(_workspace_payload())
    workspace.insert(ignore_permissions=True)


CHILD_TABLES = (
    "Livestock Settlement Line",
    "Herd Batch Line",
    "Livestock Dispatch Line",
)


def ensure_child_table_schema():
    required_columns = {
        "parent": "varchar(140)",
        "parentfield": "varchar(140)",
        "parenttype": "varchar(140)",
    }

    for doctype in CHILD_TABLES:
        if frappe.db.exists("DocType", doctype):
            frappe.db.set_value("DocType", doctype, "istable", 1, update_modified=False)

        for column_name, column_type in required_columns.items():
            try:
                frappe.db.sql_ddl(
                    f"ALTER TABLE `tab{doctype}` ADD COLUMN `{column_name}` {column_type}"
                )
            except Exception as error:
                if "Duplicate column name" not in str(error):
                    raise

    frappe.clear_cache()
