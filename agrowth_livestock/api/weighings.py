import frappe
from frappe.utils import cint


WEIGHING_FIELDS = [
    "name",
    "company",
    "herd",
    "event_date",
    "total_kg",
    "avg_weight",
    "notes",
]


def _map_weighing(row):
    return {
        "id": str(row.get("name") or ""),
        "herdId": str(row.get("herd") or ""),
        "eventDate": str(row.get("event_date") or row.get("creation") or ""),
        "totalKg": float(row.get("total_kg") or 0),
        "avgWeight": float(row.get("avg_weight") or 0),
        "notes": str(row.get("notes") or ""),
    }


def _load_herd(company_id, herd_id):
    rows = frappe.get_all(
        "Livestock Herd",
        filters=[
            ["company", "=", company_id],
            ["name", "=", herd_id],
        ],
        fields=["name", "company", "head_count", "modified"],
        limit_page_length=1,
    )
    return rows[0] if rows else None


@frappe.whitelist()
def list_weighings(company_id, herd_id=None, page=1, limit=200):
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 500)

    filters = [["company", "=", company_id]]
    if herd_id:
        filters.append(["herd", "=", herd_id])

    rows = frappe.get_all(
        "Livestock Weighing",
        filters=filters,
        fields=WEIGHING_FIELDS,
        order_by="modified desc",
        limit_start=(page - 1) * limit,
        limit_page_length=limit,
    )
    return [_map_weighing(row) for row in rows]


@frappe.whitelist()
def create_weighing(company_id, herd_id, event_date=None, total_kg=None, head_count=None, avg_weight=None, notes=None):
    herd = _load_herd(company_id, herd_id)
    if not herd:
        return None

    total_kg = float(total_kg or 0)
    head_count_value = cint(head_count or 0)
    avg_weight_value = float(avg_weight or 0)
    if head_count_value > 0:
        avg_weight_value = total_kg / head_count_value

    doc = frappe.get_doc(
        {
            "doctype": "Livestock Weighing",
            "company": company_id,
            "herd": herd_id,
            "event_date": event_date or frappe.utils.nowdate(),
            "total_kg": total_kg,
            "avg_weight": avg_weight_value,
            "notes": notes or None,
        }
    )
    doc.insert()
    return _map_weighing(doc.as_dict())
