import frappe
from frappe.utils import cint, nowdate

LIVESTOCK_HERD_DOCTYPE = "Livestock Herd"
LIVESTOCK_MOVEMENT_DOCTYPE = "Livestock Movement"
HERD_BATCH_DOCTYPE = "Herd Batch"

MOVEMENT_FIELDS = [
    "name",
    "company",
    "herd",
    "event_type",
    "event_date",
    "head_count_change",
    "weight_kg",
    "notes",
]


def _is_doctype_unavailable(exc):
    text = str(exc).lower()
    return (
        "does not exist" in text
        or "no existe" in text
        or "missing" in text
        or "not found" in text
        or "module import failed" in text
        or "no module named" in text
    )


def _map_movement(row):
    return {
        "id": str(row.get("name") or ""),
        "herdId": str(row.get("herd") or ""),
        "eventType": str(row.get("event_type") or ""),
        "eventDate": str(row.get("event_date") or row.get("creation") or ""),
        "headCountChange": float(row.get("head_count_change") or 0),
        "weightKg": float(row.get("weight_kg") or 0),
        "notes": str(row.get("notes") or ""),
    }


def _apply_head_count_delta(current_head_count, delta):
    next_value = float(current_head_count or 0) + float(delta or 0)
    if next_value < 0:
        raise ValueError("El movimiento no puede dejar stock negativo")
    return next_value


def _get_rows(doctype, filters, fields, limit_page_length=1, limit_start=0, order_by=None):
    kwargs = {
        "filters": filters,
        "fields": fields,
        "limit_page_length": limit_page_length,
    }
    if limit_start:
        kwargs["limit_start"] = limit_start
    if order_by:
        kwargs["order_by"] = order_by
    return frappe.get_all(doctype, **kwargs)


def _load_existing_herd_with_compat(company_id, herd_id):
    try:
        rows = _get_rows(
            LIVESTOCK_HERD_DOCTYPE,
            [["company", "=", company_id], ["name", "=", herd_id]],
            ["name", "company", "head_count", "modified", "status"],
            limit_page_length=1,
        )
        return {
            "herd_doctype": LIVESTOCK_HERD_DOCTYPE,
            "head_count_field": "head_count",
            "row": rows[0] if rows else None,
        }
    except Exception as exc:
        if not _is_doctype_unavailable(exc):
            raise

    rows = _get_rows(
        HERD_BATCH_DOCTYPE,
        [["company", "=", company_id], ["name", "=", herd_id]],
        ["name", "company", "total_heads", "modified", "notes", "batch_name", "status"],
        limit_page_length=1,
    )
    return {
        "herd_doctype": HERD_BATCH_DOCTYPE,
        "head_count_field": "total_heads",
        "row": rows[0] if rows else None,
    }


@frappe.whitelist()
def list_movements(company_id, herd_id=None, page=1, limit=200):
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 500)
    filters = [["company", "=", company_id]]
    if herd_id:
        filters.append(["herd", "=", herd_id])

    try:
        rows = _get_rows(
            LIVESTOCK_MOVEMENT_DOCTYPE,
            filters,
            MOVEMENT_FIELDS,
            limit_page_length=limit,
            limit_start=(page - 1) * limit,
            order_by="modified desc",
        )
    except Exception as exc:
        if not _is_doctype_unavailable(exc):
            raise
        return []

    return [_map_movement(row) for row in rows]


@frappe.whitelist()
def create_movement(company_id, herd_id, event_type, event_date=None, head_count_change=None, weight_kg=None, notes=None):
    herd = _load_existing_herd_with_compat(company_id, herd_id)
    current_row = herd.get("row")
    if not current_row:
        return None

    head_count_change = float(head_count_change or 0)
    next_head_count = _apply_head_count_delta(current_row.get(herd["head_count_field"]), head_count_change)
    event_date = event_date or nowdate()
    weight_kg = float(weight_kg or 0)

    if herd["herd_doctype"] == HERD_BATCH_DOCTYPE:
        if head_count_change != 0:
            batch = frappe.get_doc(HERD_BATCH_DOCTYPE, herd_id)
            batch.total_heads = next_head_count
            batch.save()

        return {
            "id": f"compat-{herd_id}-{frappe.generate_hash(length=8)}",
            "herdId": herd_id,
            "eventType": str(event_type or ""),
            "eventDate": str(event_date),
            "headCountChange": head_count_change,
            "weightKg": weight_kg,
            "notes": str(notes or "Compat Herd Batch movement"),
        }

    doc = frappe.get_doc(
        {
            "doctype": LIVESTOCK_MOVEMENT_DOCTYPE,
            "company": company_id,
            "herd": herd_id,
            "event_type": event_type,
            "event_date": event_date,
            "head_count_change": head_count_change,
            "weight_kg": weight_kg,
            "notes": notes or None,
        }
    )
    doc.insert()

    if head_count_change != 0:
        herd_doc = frappe.get_doc(LIVESTOCK_HERD_DOCTYPE, herd_id)
        herd_doc.head_count = next_head_count
        herd_doc.save()

    return _map_movement(doc.as_dict())
