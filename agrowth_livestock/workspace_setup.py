import frappe


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
        "content": "[]",
    }


def ensure_workspaces():
    workspace_name = "Ganadería"
    if frappe.db.exists("Workspace", workspace_name):
        return

    workspace = frappe.get_doc(_workspace_payload())
    workspace.insert(ignore_permissions=True)
