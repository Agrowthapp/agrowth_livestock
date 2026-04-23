import frappe
from frappe.utils import cint


HEALTH_FIELDS = [
    "name",
    "company",
    "herd",
    "event_date",
    "treatment",
    "notes",
]


def _map_health_event(row):
    return {
        "id": str(row.get("name") or ""),
        "herdId": str(row.get("herd") or ""),
        "eventDate": str(row.get("event_date") or row.get("creation") or ""),
        "treatment": str(row.get("treatment") or ""),
        "notes": str(row.get("notes") or ""),
    }


def _load_herd(company_id, herd_id):
    rows = frappe.get_all(
        "Livestock Herd",
        filters=[
            ["company", "=", company_id],
            ["name", "=", herd_id],
        ],
        fields=["name", "company", "modified"],
        limit_page_length=1,
    )
    return rows[0] if rows else None


@frappe.whitelist()
def list_health_events(company_id, herd_id=None, page=1, limit=200):
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 500)

    filters = [["company", "=", company_id]]
    if herd_id:
        filters.append(["herd", "=", herd_id])

    rows = frappe.get_all(
        "Livestock Health Event",
        filters=filters,
        fields=HEALTH_FIELDS,
        order_by="modified desc",
        limit_start=(page - 1) * limit,
        limit_page_length=limit,
    )
    return [_map_health_event(row) for row in rows]


@frappe.whitelist()
def create_health_event(company_id, herd_id, event_date=None, treatment=None, notes=None):
    herd = _load_herd(company_id, herd_id)
    if not herd:
        return None

    doc = frappe.get_doc(
        {
            "doctype": "Livestock Health Event",
            "company": company_id,
            "herd": herd_id,
            "event_date": event_date or frappe.utils.nowdate(),
            "treatment": treatment or None,
            "notes": notes or None,
        }
    )
    doc.insert()
    return _map_health_event(doc.as_dict())
