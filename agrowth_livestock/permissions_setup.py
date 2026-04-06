import frappe
from frappe.utils import cint

MANAGED_PERMISSION_FIELDS = (
    "select",
    "read",
    "write",
    "create",
    "delete",
    "submit",
    "cancel",
    "amend",
    "report",
    "export",
    "import",
    "share",
    "print",
    "email",
)

ANIMAL_PERMISSION_BLUEPRINT = [
    {
        "role": "System Manager",
        "read": 1,
        "write": 1,
        "create": 1,
        "delete": 1,
        "report": 1,
        "export": 1,
        "share": 1,
        "print": 1,
        "email": 1,
    },
    {
        "role": "Tenant Owner",
        "read": 1,
        "write": 1,
        "create": 1,
        "delete": 1,
        "report": 1,
        "export": 1,
        "share": 1,
        "print": 1,
        "email": 1,
    },
    {
        "role": "Livestock Manager",
        "read": 1,
        "write": 1,
        "create": 1,
        "delete": 1,
        "report": 1,
        "export": 1,
        "share": 1,
        "print": 1,
        "email": 1,
    },
    {
        "role": "Livestock User",
        "read": 1,
        "write": 1,
        "create": 1,
        "report": 1,
        "export": 1,
        "share": 1,
        "print": 1,
        "email": 1,
    },
    {
        "role": "Stock Manager",
        "read": 1,
        "report": 1,
        "print": 1,
    },
    {
        "role": "Stock User",
        "read": 1,
        "report": 1,
        "print": 1,
    },
]

ANIMAL_EVENT_PERMISSION_BLUEPRINT = [
    {
        "role": "System Manager",
        "read": 1,
        "write": 1,
        "create": 1,
        "delete": 1,
        "report": 1,
        "export": 1,
        "share": 1,
        "print": 1,
        "email": 1,
    },
    {
        "role": "Tenant Owner",
        "read": 1,
        "write": 1,
        "create": 1,
        "delete": 1,
        "report": 1,
        "export": 1,
        "share": 1,
        "print": 1,
        "email": 1,
    },
    {
        "role": "Livestock Manager",
        "read": 1,
        "write": 1,
        "create": 1,
        "delete": 1,
        "report": 1,
        "export": 1,
        "share": 1,
        "print": 1,
        "email": 1,
    },
    {
        "role": "Livestock User",
        "read": 1,
        "write": 1,
        "create": 1,
        "report": 1,
        "export": 1,
        "share": 1,
        "print": 1,
        "email": 1,
    },
]

TOP_LEVEL_BLUEPRINTS = {
    "Animal": ANIMAL_PERMISSION_BLUEPRINT,
    "Animal Event": ANIMAL_EVENT_PERMISSION_BLUEPRINT,
}


def _permission_signature(row):
    if isinstance(row, dict):
        role = row.get("role")
        permlevel = cint(row.get("permlevel", 0) or 0)
    else:
        role = getattr(row, "role", None)
        permlevel = cint(getattr(row, "permlevel", 0) or 0)
    return role, permlevel



def _normalize_permission_row(row):
    normalized = {
        "role": row["role"],
        "permlevel": cint(row.get("permlevel", 0) or 0),
        "if_owner": cint(row.get("if_owner", 0) or 0),
    }
    for fieldname in MANAGED_PERMISSION_FIELDS:
        normalized[fieldname] = cint(row.get(fieldname, 0) or 0)
    return normalized



def _list_custom_docperms(doctype_name):
    fields = ["name", "parent", "role", "permlevel", "if_owner", *MANAGED_PERMISSION_FIELDS]
    return frappe.get_all(
        "Custom DocPerm",
        filters={"parent": doctype_name, "permlevel": 0},
        fields=fields,
        limit_page_length=0,
    )



def _insert_custom_docperm(doctype_name, row):
    payload = {
        "doctype": "Custom DocPerm",
        "parent": doctype_name,
        **row,
    }
    frappe.get_doc(payload).insert(ignore_permissions=True)



def _update_custom_docperm(name, row):
    doc = frappe.get_doc("Custom DocPerm", name)
    doc.update(row)
    doc.save(ignore_permissions=True)



def _sync_doctype_permissions(doctype_name, desired_rows):
    if not frappe.db.exists("DocType", doctype_name):
        return False

    desired_rows = [_normalize_permission_row(row) for row in desired_rows]
    desired_by_signature = {_permission_signature(row): row for row in desired_rows}
    managed_signatures = set(desired_by_signature)
    managed_roles = {row["role"] for row in desired_rows}

    changed = False
    existing_rows = _list_custom_docperms(doctype_name)

    for row in existing_rows:
        signature = _permission_signature(row)
        if signature in managed_signatures:
            desired = desired_by_signature[signature]
            updates = {}
            for fieldname in ("if_owner", *MANAGED_PERMISSION_FIELDS):
                desired_value = cint(desired.get(fieldname, 0) or 0)
                current_value = cint(row.get(fieldname, 0) or 0)
                if current_value != desired_value:
                    updates[fieldname] = desired_value
            if updates:
                _update_custom_docperm(row["name"], updates)
                changed = True
        elif row.get("role") in managed_roles:
            frappe.delete_doc("Custom DocPerm", row["name"], ignore_permissions=True)
            changed = True

    existing_signatures = {_permission_signature(row) for row in _list_custom_docperms(doctype_name)}
    for signature, desired in desired_by_signature.items():
        if signature in existing_signatures:
            continue
        _insert_custom_docperm(doctype_name, desired)
        changed = True

    if changed:
        frappe.clear_cache(doctype=doctype_name)

    return changed



def _iter_livestock_child_tables():
    return frappe.get_all(
        "DocType",
        filters={"module": "Livestock", "istable": 1},
        pluck="name",
        limit_page_length=0,
    )



def _iter_parent_doctypes_for_child(child_doctype):
    parent_doctypes = []
    for doctype_name in frappe.get_all(
        "DocType",
        filters={"module": "Livestock", "istable": 0},
        pluck="name",
        limit_page_length=0,
    ):
        meta = frappe.get_meta(doctype_name)
        for field in meta.fields or []:
            if field.fieldtype in ("Table", "Table MultiSelect") and field.options == child_doctype:
                parent_doctypes.append(doctype_name)
                break
    return parent_doctypes



def _build_child_table_permission_rows(child_doctype):
    roles = set()
    for parent_doctype in _iter_parent_doctypes_for_child(child_doctype):
        parent_doc = frappe.get_doc("DocType", parent_doctype)
        for row in parent_doc.permissions or []:
            if cint(getattr(row, "permlevel", 0) or 0) != 0:
                continue
            if cint(getattr(row, "read", 0) or 0) != 1:
                continue
            role = getattr(row, "role", None)
            if role:
                roles.add(role)

        for row in _list_custom_docperms(parent_doctype):
            if cint(row.get("read", 0) or 0) != 1:
                continue
            role = row.get("role")
            if role:
                roles.add(role)

    return [
        {
            "role": role,
            "read": 1,
            "report": 1,
            "print": 1,
        }
        for role in sorted(roles)
    ]



def ensure_livestock_permissions():
    changed = False

    for doctype_name, blueprint in TOP_LEVEL_BLUEPRINTS.items():
        changed = _sync_doctype_permissions(doctype_name, blueprint) or changed

    for child_doctype in _iter_livestock_child_tables():
        desired_rows = _build_child_table_permission_rows(child_doctype)
        if not desired_rows:
            continue
        changed = _sync_doctype_permissions(child_doctype, desired_rows) or changed

    if changed:
        frappe.db.commit()

    return changed
