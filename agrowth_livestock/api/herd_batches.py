import frappe
from frappe.utils import cint, now

BATCH_FIELDS = [
    "name",
    "company",
    "warehouse",
    "status",
    "arrival_date",
    "origin_type",
    "origin_document",
    "notes",
    "total_heads",
    "total_weight",
]

LINE_FIELDS = [
    "name",
    "parent",
    "item_code",
    "species",
    "category",
    "qty_heads",
    "avg_weight",
    "total_weight",
    "unit_price",
    "amount",
]

STANDARD_FIELDS = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "parent", "parentfield", "parenttype"}


def _existing_fields(doctype, requested_fields):
    meta = frappe.get_meta(doctype)
    return [field for field in requested_fields if field in STANDARD_FIELDS or meta.get_field(field)]


def _parse_lines(lines):
    if lines is None:
        return []

    if isinstance(lines, str):
        try:
            lines = frappe.parse_json(lines)
        except Exception:
            return []

    if not isinstance(lines, (list, tuple)):
        return []

    parsed = []
    for row in lines:
        if not isinstance(row, dict):
            continue
        parsed.append(
            {
                "item_code": row.get("itemCode") or row.get("item_code"),
                "species": row.get("species"),
                "category": row.get("category"),
                "qty_heads": cint(row.get("qtyHeads") or row.get("qty_heads") or 0),
                "avg_weight": row.get("avgWeight") or row.get("avg_weight") or None,
                "unit_price": row.get("unitPrice") or row.get("unit_price") or None,
            }
        )
    return parsed


def _map_line(row):
    return {
        "id": str(row.get("name") or ""),
        "itemCode": str(row.get("item_code") or ""),
        "species": str(row.get("species") or ""),
        "category": str(row.get("category") or ""),
        "qtyHeads": cint(row.get("qty_heads") or 0),
        "avgWeight": float(row.get("avg_weight")) if row.get("avg_weight") not in (None, "") else None,
        "totalWeight": float(row.get("total_weight")) if row.get("total_weight") not in (None, "") else None,
        "unitPrice": float(row.get("unit_price")) if row.get("unit_price") not in (None, "") else None,
        "amount": float(row.get("amount")) if row.get("amount") not in (None, "") else None,
    }


def _map_batch(row, lines=None):
    lines = lines or []
    total_heads_from_lines = sum(cint(line.get("qtyHeads") or 0) for line in lines)
    total_weight_from_lines = sum(float(line.get("totalWeight") or 0) for line in lines)
    total_heads = total_heads_from_lines or cint(row.get("total_heads") or 0)
    total_weight = total_weight_from_lines or float(row.get("total_weight") or 0)

    return {
        "id": str(row.get("name") or ""),
        "name": str(row.get("name") or ""),
        "company": str(row.get("company") or ""),
        "warehouse": str(row.get("warehouse") or ""),
        "status": row.get("status") or "Pending Entry",
        "arrivalDate": str(row.get("arrival_date") or ""),
        "originType": row.get("origin_type") or "Other",
        "originDocument": row.get("origin_document") or None,
        "notes": row.get("notes") or None,
        "totalHeads": total_heads,
        "totalWeight": total_weight if total_weight > 0 else None,
        "lines": lines,
    }


def _load_batch(company_id, batch_id):
    rows = frappe.get_all(
        "Herd Batch",
        filters=[["company", "=", company_id], ["name", "=", batch_id]],
        fields=_existing_fields("Herd Batch", BATCH_FIELDS),
        limit_page_length=1,
    )
    return rows[0] if rows else None


def _load_lines_by_batch(batch_ids):
    if not batch_ids:
        return {}

    rows = frappe.get_all(
        "Herd Batch Line",
        filters=[["parent", "in", batch_ids]],
        fields=_existing_fields("Herd Batch Line", LINE_FIELDS),
        limit_page_length=max(len(batch_ids) * 20, 500),
        order_by="idx asc",
    )

    grouped = {}
    for row in rows:
        parent = str(row.get("parent") or "")
        if not parent:
            continue
        grouped.setdefault(parent, []).append(_map_line(row))
    return grouped


def _append_rejection_note(existing_notes, rejection_notes):
    entry = "[INGRESO_RECHAZADO]"
    if rejection_notes and str(rejection_notes).strip():
        entry = f"{entry} {str(rejection_notes).strip()}"

    if not existing_notes:
        return entry

    normalized = str(existing_notes).rstrip()
    return f"{normalized}\n{entry}"


@frappe.whitelist()
def list_herd_batches(company_id, status=None, search=None, page=1, limit=200):
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 500)

    filters = [["company", "=", company_id]]
    if status:
        filters.append(["status", "=", status])
    if search:
        filters.append(["name", "like", f"%{search}%"])

    rows = frappe.get_all(
        "Herd Batch",
        filters=filters,
        fields=_existing_fields("Herd Batch", BATCH_FIELDS),
        order_by="modified desc",
        limit_start=(page - 1) * limit,
        limit_page_length=limit,
    )
    lines_by_batch = _load_lines_by_batch([str(row.get("name") or "") for row in rows])
    return [_map_batch(row, lines_by_batch.get(str(row.get("name") or ""), [])) for row in rows]


@frappe.whitelist()
def get_herd_batch(company_id, batch_id):
    row = _load_batch(company_id, batch_id)
    if not row:
        return None
    lines_by_batch = _load_lines_by_batch([batch_id])
    return _map_batch(row, lines_by_batch.get(batch_id, []))


@frappe.whitelist()
def create_herd_batch(company_id, warehouse, arrival_date, origin_type, lines, origin_document=None, notes=None):
    parsed_lines = _parse_lines(lines)
    if not warehouse:
        frappe.throw("warehouse es requerido")
    if not arrival_date:
        frappe.throw("arrivalDate es requerido")
    if not origin_type:
        frappe.throw("originType es requerido")
    if not parsed_lines:
        frappe.throw("Al menos una línea es requerida")

    doc = frappe.get_doc(
        {
            "doctype": "Herd Batch",
            "company": company_id,
            "warehouse": warehouse,
            "arrival_date": arrival_date,
            "origin_type": origin_type,
            "origin_document": origin_document,
            "notes": notes,
            "lines": parsed_lines,
        }
    )
    doc.insert()
    return get_herd_batch(company_id, doc.name)


@frappe.whitelist()
def update_herd_batch(company_id, batch_id, warehouse=None, arrival_date=None, origin_type=None, origin_document=None, notes=None, status=None):
    row = _load_batch(company_id, batch_id)
    if not row:
        return None

    doc = frappe.get_doc("Herd Batch", batch_id)
    if warehouse is not None:
        doc.warehouse = warehouse
    if arrival_date is not None:
        doc.arrival_date = arrival_date
    if origin_type is not None:
        doc.origin_type = origin_type
    if origin_document is not None:
        doc.origin_document = origin_document
    if notes is not None:
        doc.notes = notes
    if status is not None:
        doc.status = status
    doc.save()
    return get_herd_batch(company_id, batch_id)


@frappe.whitelist()
def close_herd_batch(company_id, batch_id):
    row = _load_batch(company_id, batch_id)
    if not row:
        return False

    doc = frappe.get_doc("Herd Batch", batch_id)
    doc.status = "Closed"
    doc.save()
    return True


def _set_confirmation_fields(doc, *, status, mode, ts):
    """
    Set the confirmation_* fields on a Herd Batch doc defensively.

    Pre-PR2 Herd Batch schemas (and partially-migrated sites) may not have
    `confirmation_status`, `confirmation_mode`, or `confirmed_at` declared.
    Writing to a missing field raises an AttributeError that Frappe turns
    into HTTP 417. Guard every write with `meta.get_field` so the API
    degrades gracefully on legacy schemas and the BFF can still surface
    a useful response.
    """
    meta = frappe.get_meta(doc.doctype)
    if meta.get_field("confirmation_status") and hasattr(doc, "confirmation_status"):
        doc.confirmation_status = status
    if meta.get_field("confirmation_mode") and hasattr(doc, "confirmation_mode"):
        doc.confirmation_mode = mode
    if meta.get_field("confirmed_at") and hasattr(doc, "confirmed_at"):
        doc.confirmed_at = ts


@frappe.whitelist()
def confirm_herd_batch(company_id, batch_id, status="Completed", mode="None", notes=None):
    row = _load_batch(company_id, batch_id)
    if not row:
        return None

    if status not in {"Completed", "Rejected"}:
        frappe.throw("status debe ser Completed o Rejected")
    if status == "Completed" and mode not in {"None", "Manual", "CSV EID"}:
        frappe.throw("mode invalido para confirmación")

    doc = frappe.get_doc("Herd Batch", batch_id)
    if status == "Rejected":
        _set_confirmation_fields(doc, status="Rejected", mode="Rejected", ts=now())
        if notes is not None:
            doc.notes = _append_rejection_note(doc.notes, notes)
    else:
        doc.status = "Active"
        _set_confirmation_fields(doc, status="Completed", mode=mode, ts=now())

    doc.save()

    return {
        "id": str(doc.name or batch_id),
        "status": str(doc.status or row.get("status") or "Pending Entry"),
        "confirmation_status": str(getattr(doc, "confirmation_status", None) or status),
        "confirmation_mode": str(getattr(doc, "confirmation_mode", None) or mode),
    }
