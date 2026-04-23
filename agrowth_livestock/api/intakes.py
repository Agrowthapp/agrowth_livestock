import json
import re

import frappe
from frappe.utils import cint

RECEIVED_ANIMAL_STATUSES = {
    "Normal",
    "Lastimado",
    "Problema sanitario",
    "Bajo observación",
}
DISCREPANCY_STATUSES = ["Con discrepancia", "Ingreso con discrepancias"]
VALID_HISTORY_STATUSES = ["Confirmado", "Parcialmente recibido", "Con discrepancia", "Ingreso con discrepancias", "Revertido"]

STANDARD_FIELDS = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "parent", "parentfield", "parenttype"}


def _existing_fields(doctype, requested_fields):
    meta = frappe.get_meta(doctype)
    return [field for field in requested_fields if field in STANDARD_FIELDS or meta.get_field(field)]


def _parse_json(value, default=None):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return frappe.parse_json(value)
        except Exception:
            return default
    return value


def _parse_notes_audit(notes):
    timeline = []
    for raw_line in str(notes or "").splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("["):
            continue
        match = re.match(r"^\[(?P<ts>[^\]]+)\]\s+(?P<action>.+?)\s+by\s+(?P<actor>[^-]+?)(?:\s+-\s+(?P<payload>.+))?$", line)
        if not match:
            continue
        payload_summary = match.group("payload")
        timeline.append(
            {
                "action": match.group("action").strip(),
                "actor": match.group("actor").strip(),
                "at": match.group("ts").strip(),
                "payload_summary": payload_summary.strip() if payload_summary else None,
            }
        )
    return timeline


def _map_intake_line(line):
    return {
        "category": str(line.get("category") or ""),
        "sex": str(line.get("sex") or ""),
        "expectedQuantity": cint(line.get("expected_quantity") or line.get("expected_heads") or 0),
        "receivedQuantity": cint(line.get("received_quantity") or line.get("received_heads") or 0),
        "discrepancy": cint(line.get("discrepancy") or 0),
    }


def _map_intake_animal(animal):
    return {
        "earTagId": str(animal.get("ear_tag_id") or ""),
        "category": animal.get("category") or None,
        "sex": animal.get("sex") or None,
        "status": str(animal.get("status") or animal.get("state") or "Normal"),
        "observation": animal.get("observation") or animal.get("notes") or None,
        "weight": float(animal.get("weight")) if animal.get("weight") not in (None, "") else None,
        "batchLineRef": animal.get("batch_line_ref") or None,
        "isDuplicateInUpload": bool(animal.get("is_duplicate_in_upload")),
        "matchesExistingAnimal": animal.get("matches_existing_animal") or None,
    }


def _map_intake(doc):
    row = doc.as_dict() if callable(getattr(doc, "as_dict", None)) else doc
    lines = [_map_intake_line(line) for line in (row.get("lines") or [])]
    animals = [_map_intake_animal(animal) for animal in (row.get("animals") or [])]
    total_expected = cint(row.get("total_expected") or row.get("expected_heads") or 0)
    total_received = cint(row.get("total_received") or row.get("received_heads") or 0)
    total_not_received = row.get("total_not_received")
    if total_not_received in (None, ""):
        total_not_received = row.get("missing_heads")
    if total_not_received in (None, ""):
        total_not_received = max(0, total_expected - total_received)

    return {
        "id": str(row.get("name") or ""),
        "name": str(row.get("name") or ""),
        "company": str(row.get("company") or ""),
        "status": str(row.get("status") or ""),
        "livestockSettlement": row.get("settlement") or row.get("livestock_settlement") or None,
        "herdBatch": row.get("herd_batch") or None,
        "warehouse": row.get("warehouse") or None,
        "expectedDate": str(row.get("expected_date") or row.get("posting_date")) if (row.get("expected_date") or row.get("posting_date")) else None,
        "confirmedDate": str(row.get("confirmed_date")) if row.get("confirmed_date") else (str(row.get("confirmed_at")) if row.get("confirmed_at") else None),
        "requiresIndividualization": bool(row.get("requires_individualization")),
        "lines": lines,
        "animals": animals,
        "totalExpected": total_expected,
        "totalReceived": total_received,
        "totalDiscrepancy": cint(row.get("total_discrepancy") or (total_expected - total_received)),
        "totalNotReceived": cint(total_not_received or 0),
        "discrepancyType": row.get("discrepancy_type") or None,
        "discrepancyNotes": row.get("discrepancy_notes") or None,
        "adminResolutionStatus": row.get("admin_resolution_status") or None,
        "auditTrail": row.get("audit_trail") or row.get("notes") or None,
        "createdAt": str(row.get("creation") or ""),
        "modifiedAt": str(row.get("modified") or ""),
    }


def _map_history_detail(doc):
    row = doc.as_dict() if callable(getattr(doc, "as_dict", None)) else doc
    audit_log = _parse_json(row.get("audit_log"), None)
    if isinstance(audit_log, list):
        timeline = [
            {
                "action": str(item.get("action") or ""),
                "actor": item.get("actor") or item.get("user") or None,
                "at": item.get("at") or item.get("timestamp") or None,
                "reason": item.get("reason") or None,
                "payload_summary": item.get("payload_summary") or None,
            }
            for item in audit_log
            if isinstance(item, dict)
        ]
    else:
        timeline = _parse_notes_audit(row.get("notes"))

    return {
        "id": str(row.get("name") or ""),
        "name": str(row.get("name") or ""),
        "status": str(row.get("status") or ""),
        "notes": row.get("notes") or None,
        "timeline": timeline,
        "audit_log": timeline,
    }


def _map_discrepancy(row):
    total_expected = cint(row.get("total_expected") or row.get("expected_heads") or 0)
    total_received = cint(row.get("total_received") or row.get("received_heads") or 0)
    total_not_received = row.get("total_not_received")
    if total_not_received in (None, ""):
        total_not_received = row.get("missing_heads")
    if total_not_received in (None, ""):
        total_not_received = max(0, total_expected - total_received)

    return {
        "id": str(row.get("name") or ""),
        "name": str(row.get("name") or ""),
        "company": str(row.get("company") or ""),
        "status": str(row.get("status") or ""),
        "livestockSettlement": row.get("settlement") or row.get("livestock_settlement") or None,
        "receptionDate": str(row.get("reception_date") or row.get("posting_date")) if (row.get("reception_date") or row.get("posting_date")) else None,
        "totalExpected": total_expected,
        "totalReceived": total_received,
        "totalNotReceived": cint(total_not_received or 0),
        "discrepancyType": row.get("discrepancy_type") or None,
        "discrepancyNotes": row.get("discrepancy_notes") or None,
        "adminResolutionStatus": row.get("admin_resolution_status") or None,
        "createdAt": str(row.get("creation") or ""),
    }


def _load_intake(company_id, intake_id):
    if not frappe.db.exists("Livestock Intake", intake_id):
        return None
    doc = frappe.get_doc("Livestock Intake", intake_id)
    if str(doc.company or "") != str(company_id or ""):
        return None
    return doc


def _parse_ear_tag_csv(content):
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    if not lines:
        return []
    headers = [part.strip() for part in lines[0].split(",")]
    if "ear_tag_id" not in headers:
        frappe.throw("CSV must include ear_tag_id header")
    index = headers.index("ear_tag_id")
    values = []
    for row in lines[1:]:
        parts = [part.strip() for part in row.split(",")]
        if index < len(parts) and parts[index]:
            values.append(parts[index])
    return values


def _summary_from_animals(doc):
    animals = doc.get("animals") or []
    total_expected = cint(doc.get("total_expected") or doc.get("expected_heads") or 0)
    total_received = sum(1 for animal in animals if (animal.get("status") or "Normal") in RECEIVED_ANIMAL_STATUSES)
    total_not_received = max(len(animals) - total_received, 0)
    return {
        "total_expected": total_expected,
        "total_received": total_received,
        "total_not_received": total_not_received,
        "has_discrepancy": total_expected != total_received,
    }


@frappe.whitelist()
def list_intakes(company_id, status=None, livestock_settlement=None, warehouse=None, page=1, limit=20):
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 200)
    filters = [["company", "=", company_id]]
    if status:
        filters.append(["status", "=", status])
    if livestock_settlement:
        filters.append(["settlement", "=", livestock_settlement])
    if warehouse:
        filters.append(["warehouse", "=", warehouse])

    rows = frappe.get_all(
        "Livestock Intake",
        filters=filters,
        fields=_existing_fields(
            "Livestock Intake",
            [
                "name",
                "company",
                "status",
                "settlement",
                "herd_batch",
                "warehouse",
                "posting_date",
                "expected_date",
                "confirmed_date",
                "confirmed_at",
                "requires_individualization",
                "expected_heads",
                "received_heads",
                "missing_heads",
                "total_expected",
                "total_received",
                "total_discrepancy",
                "total_not_received",
                "admin_resolution_status",
                "notes",
                "creation",
                "modified",
            ],
        ),
        order_by="modified desc",
        limit_start=(page - 1) * limit,
        limit_page_length=limit,
    )
    return [_map_intake(row) for row in rows]


@frappe.whitelist()
def get_intake(company_id, intake_id):
    doc = _load_intake(company_id, intake_id)
    if not doc:
        return None
    return _map_intake(doc)


@frappe.whitelist()
def confirm_intake(company_id, intake_id, user, mode="None", lines=None, animals=None, notes=None):
    doc = _load_intake(company_id, intake_id)
    if not doc:
        return None
    if notes and hasattr(doc, "notes"):
        doc.notes = str(notes)
        doc.save(ignore_permissions=True)
    doc.confirm_intake(user, mode=mode or "None")
    return _map_intake(frappe.get_doc("Livestock Intake", intake_id))


@frappe.whitelist()
def revert_intake(company_id, intake_id, user, reason):
    doc = _load_intake(company_id, intake_id)
    if not doc:
        return None
    doc.revert_intake(user, reason)
    return _map_intake(frappe.get_doc("Livestock Intake", intake_id))


@frappe.whitelist()
def save_intake_animals(company_id, intake_id, user, animals):
    doc = _load_intake(company_id, intake_id)
    if not doc:
        return None
    parsed_animals = _parse_json(animals, []) or []
    staged = doc.stage_animals(user, parsed_animals, source="manual")
    return {
        "name": staged.name,
        "animals": [row.as_dict() for row in staged.animals or []],
        "audit_log": [
            {
                "action": "animals_loaded_manual",
                "actor": user,
                "payload_summary": f"{len(parsed_animals)} animals loaded manually",
            }
        ],
    }


@frappe.whitelist()
def upload_intake_animals(company_id, intake_id, user, file_content):
    doc = _load_intake(company_id, intake_id)
    if not doc:
        return None

    ear_tags = _parse_ear_tag_csv(file_content)
    duplicates_map = {}
    for ear_tag_id in ear_tags:
        duplicates_map[ear_tag_id] = duplicates_map.get(ear_tag_id, 0) + 1
    duplicates_in_file = [ear_tag_id for ear_tag_id, count in duplicates_map.items() if count > 1]

    existing_animals = frappe.get_all(
        "Animal",
        filters=[["ear_tag_id", "in", ear_tags]],
        fields=["name", "ear_tag_id"],
        limit_page_length=max(len(ear_tags), 1),
    ) if ear_tags else []
    existing_by_ear_tag = {str(row.get("ear_tag_id") or ""): str(row.get("name") or "") for row in existing_animals}

    seen = {}
    animals_payload = []
    for ear_tag_id in ear_tags:
        seen[ear_tag_id] = seen.get(ear_tag_id, 0) + 1
        animals_payload.append(
            {
                "ear_tag_id": ear_tag_id,
                "status": "Normal",
                "observation": "",
                "is_duplicate_in_upload": duplicates_map.get(ear_tag_id, 0) > 1 and seen[ear_tag_id] > 1,
                "matches_existing_animal": existing_by_ear_tag.get(ear_tag_id) or None,
            }
        )

    staged = doc.stage_animals(user, animals_payload, source="file")
    return {
        "animals": [row.as_dict() for row in staged.animals or []],
        "duplicates_in_file": duplicates_in_file,
    }


@frappe.whitelist()
def get_intake_animals_summary(company_id, intake_id):
    doc = _load_intake(company_id, intake_id)
    if not doc:
        return None
    return _summary_from_animals(doc)


@frappe.whitelist()
def get_intake_history(company_id, intake_id):
    doc = _load_intake(company_id, intake_id)
    if not doc:
        return None
    return _map_history_detail(doc)


@frappe.whitelist()
def update_intake_history(company_id, intake_id, user, notes, reason=None):
    doc = _load_intake(company_id, intake_id)
    if not doc:
        return None
    if str(doc.status or "") == "Cerrado administrativamente" or str(doc.get("admin_resolution_status") or "").lower() == "closed":
        frappe.throw("Livestock intake is administratively closed")
    doc.notes = notes
    doc.save(ignore_permissions=True)
    return {
        "name": doc.name,
        "notes": doc.notes,
        "audit_log": [
            {
                "action": "notes_updated",
                "actor": user,
                "reason": reason,
                "payload_summary": notes,
            }
        ],
    }


@frappe.whitelist()
def list_intake_discrepancies(company_id, page=1, limit=20):
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 200)
    rows = frappe.get_all(
        "Livestock Intake",
        filters=[["company", "=", company_id], ["status", "in", DISCREPANCY_STATUSES]],
        fields=_existing_fields(
            "Livestock Intake",
            [
                "name",
                "company",
                "status",
                "settlement",
                "reception_date",
                "posting_date",
                "total_expected",
                "total_received",
                "total_not_received",
                "expected_heads",
                "received_heads",
                "missing_heads",
                "discrepancy_type",
                "discrepancy_notes",
                "admin_resolution_status",
                "creation",
            ],
        ),
        order_by="modified desc",
        limit_start=(page - 1) * limit,
        limit_page_length=limit,
    )
    return [_map_discrepancy(row) for row in rows]


@frappe.whitelist()
def resolve_intake_discrepancy(company_id, intake_id, resolution, notes, user):
    doc = _load_intake(company_id, intake_id)
    if not doc:
        return None
    if str(doc.status or "") not in DISCREPANCY_STATUSES:
        frappe.throw("Cannot resolve non-discrepant intake")

    doc.status = "Cerrado administrativamente"
    if frappe.get_meta(doc.doctype).get_field("admin_resolution_status"):
        doc.admin_resolution_status = "Closed"
    if frappe.get_meta(doc.doctype).get_field("resolution"):
        doc.resolution = resolution
    if frappe.get_meta(doc.doctype).get_field("resolution_notes"):
        doc.resolution_notes = notes
    if frappe.get_meta(doc.doctype).get_field("resolution_actor"):
        doc.resolution_actor = user
    elif frappe.get_meta(doc.doctype).get_field("resolved_by"):
        doc.resolved_by = user
    if frappe.get_meta(doc.doctype).get_field("discrepancy_notes") and notes:
        doc.discrepancy_notes = notes
    doc.save(ignore_permissions=True)
    return _map_intake(doc)


@frappe.whitelist()
def list_intake_history_feed(company_id, page=1, limit=20):
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 200)

    batches = frappe.get_all(
        "Herd Batch",
        filters=[["company", "=", company_id], ["confirmation_status", "=", "Completed"]],
        fields=["name", "origin_document", "arrival_date", "confirmation_mode", "confirmed_at", "modified"],
        order_by="modified desc",
        limit_start=(page - 1) * limit,
        limit_page_length=limit,
    )
    if not batches:
        return []

    settlement_ids = list({str(batch.get("origin_document") or "") for batch in batches if batch.get("origin_document")})
    batch_ids = [str(batch.get("name") or "") for batch in batches if batch.get("name")]

    settlements = frappe.get_all(
        "Livestock Settlement",
        filters=[["name", "in", settlement_ids]],
        fields=["name", "supplier", "posting_date"],
        limit_page_length=max(len(settlement_ids), 1),
    ) if settlement_ids else []

    batch_lines = frappe.get_all(
        "Herd Batch Line",
        filters=[["parent", "in", batch_ids]],
        fields=["parent", "qty_heads"],
        limit_page_length=max(len(batch_ids) * 20, 200),
    ) if batch_ids else []

    batch_totals = frappe.get_all(
        "Herd Batch",
        filters=[["name", "in", batch_ids]],
        fields=_existing_fields("Herd Batch", ["name", "total_heads"]),
        limit_page_length=max(len(batch_ids), 1),
    ) if batch_ids else []

    settlement_by_name = {str(row.get("name") or ""): row for row in settlements}
    heads_by_batch = {}
    for line in batch_lines:
        parent = str(line.get("parent") or "")
        heads_by_batch[parent] = heads_by_batch.get(parent, 0) + cint(line.get("qty_heads") or 0)
    total_heads_by_batch = {str(row.get("name") or ""): cint(row.get("total_heads") or 0) for row in batch_totals}

    data = []
    for batch in batches:
        batch_id = str(batch.get("name") or "")
        settlement_id = str(batch.get("origin_document") or "")
        settlement = settlement_by_name.get(settlement_id, {})
        data.append(
            {
                "id": batch_id,
                "origin_document": settlement_id,
                "supplier": str(settlement.get("supplier") or ""),
                "posting_date": str(settlement.get("posting_date") or ""),
                "arrival_date": str(batch.get("arrival_date") or ""),
                "confirmation_mode": str(batch.get("confirmation_mode") or "None"),
                "confirmed_at": str(batch.get("confirmed_at") or batch.get("modified") or ""),
                "head_count": heads_by_batch.get(batch_id) or total_heads_by_batch.get(batch_id) or 0,
            }
        )
    return data
