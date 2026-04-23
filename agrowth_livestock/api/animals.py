import frappe
from frappe.utils import cint, now_datetime

PLACEHOLDER_PREFIX = "SIN-CARAVANA-"
BULK_MOVE_MAX = 200

ANIMAL_FIELDS = [
    "name",
    "ear_tag_id",
    "species",
    "sex",
    "breed",
    "current_category",
    "birth_date",
    "current_weight",
    "current_herd_batch",
    "warehouse",
    "origin_type",
    "origin_document",
    "serial_no",
    "disabled",
    "company",
]


def _to_bool(value):
    return bool(int(value)) if isinstance(value, str) and value.isdigit() else bool(value)


def _map_animal(row):
    current_weight = row.get("current_weight")
    return {
        "id": str(row.get("name") or ""),
        "earTagId": str(row.get("ear_tag_id") or ""),
        "species": row.get("species") or "Otro",
        "sex": row.get("sex") or "Desconocido",
        "breed": row.get("breed") or None,
        "currentCategory": row.get("current_category") or "Otro",
        "birthDate": str(row.get("birth_date")) if row.get("birth_date") else None,
        "currentWeight": float(current_weight) if current_weight not in (None, "") else None,
        "currentHerdBatch": row.get("current_herd_batch") or None,
        "warehouse": row.get("warehouse") or None,
        "originType": row.get("origin_type") or None,
        "originDocument": row.get("origin_document") or None,
        "serialNo": row.get("serial_no") or None,
        "disabled": _to_bool(row.get("disabled")),
        "isPlaceholder": str(row.get("ear_tag_id") or "").startswith(PLACEHOLDER_PREFIX),
    }


def _animal_filters(company_id, herd_batch=None, warehouse=None, category=None, status=None, search=None):
    filters = [["company", "=", company_id]]
    if herd_batch:
        filters.append(["current_herd_batch", "=", herd_batch])
    if warehouse:
        filters.append(["warehouse", "=", warehouse])
    if category:
        filters.append(["current_category", "=", category])
    if status == "inactive":
        filters.append(["disabled", "=", 1])
    elif status != "all":
        filters.append(["disabled", "=", 0])
    if search:
        filters.append(["ear_tag_id", "like", f"%{search}%"])
    return filters


def _load_animal(company_id, animal_id):
    rows = frappe.get_all(
        "Animal",
        filters=[["company", "=", company_id], ["name", "=", animal_id]],
        fields=ANIMAL_FIELDS,
        limit_page_length=1,
    )
    return rows[0] if rows else None


def _load_corral(company_id, corral_id):
    rows = frappe.get_all(
        "Warehouse",
        filters=[
            ["name", "=", corral_id],
            ["company", "=", company_id],
            ["is_corral", "=", 1],
            ["disabled", "=", 0],
        ],
        fields=["name", "warehouse_name"],
        limit_page_length=1,
    )
    return rows[0] if rows else None


def _create_animal_event(animal_id, event_type, event_date=None, notes=None, **extra_fields):
    payload = {
        "doctype": "Animal Event",
        "animal": animal_id,
        "event_type": event_type,
        "event_date": event_date or str(now_datetime()).replace("T", " ")[:19],
        "notes": notes or None,
    }
    payload.update(extra_fields)
    doc = frappe.get_doc(payload)
    doc.insert()
    return doc


def _sync_herd_batch_warehouse_if_needed(company_id, herd_batch_id, target_corral_id):
    if not herd_batch_id:
        return

    rows = frappe.get_all(
        "Herd Batch",
        filters=[["name", "=", herd_batch_id], ["company", "=", company_id]],
        fields=["name", "warehouse"],
        limit_page_length=1,
    )
    if not rows:
        return

    current_warehouse = str(rows[0].get("warehouse") or "")
    if current_warehouse == target_corral_id:
        return

    herd_batch = frappe.get_doc("Herd Batch", herd_batch_id)
    herd_batch.warehouse = target_corral_id
    herd_batch.save()


def _move_animal_record(company_id, row, target_corral_id, event_date=None, notes=None):
    animal_id = str(row.get("name") or "")
    from_corral = str(row.get("warehouse") or "")

    if from_corral == target_corral_id:
        return {"ok": False, "error": "SAME_CORRAL"}

    _sync_herd_batch_warehouse_if_needed(
        company_id,
        row.get("current_herd_batch"),
        target_corral_id,
    )

    animal = frappe.get_doc("Animal", animal_id)
    animal.warehouse = target_corral_id
    animal.save()

    _create_animal_event(
        animal_id,
        "Movimiento",
        event_date=event_date,
        notes=notes or f"Movido de {from_corral} a {target_corral_id}",
        new_warehouse=target_corral_id,
    )

    return {
        "ok": True,
        "result": {
            "animalId": animal_id,
            "earTagId": str(row.get("ear_tag_id") or ""),
            "fromCorral": from_corral,
            "toCorral": target_corral_id,
        },
    }


def _parse_animal_ids(animal_ids):
    if animal_ids is None:
        return []

    if isinstance(animal_ids, str):
        try:
            animal_ids = frappe.parse_json(animal_ids)
        except Exception:
            animal_ids = [animal_ids]

    if not isinstance(animal_ids, (list, tuple)):
        return []

    parsed = []
    for item in animal_ids:
        value = str(item or "").strip()
        if value:
            parsed.append(value)
    return parsed


@frappe.whitelist()
def list_animals(company_id, herd_batch=None, warehouse=None, category=None, status=None, search=None, page=1, limit=200):
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 500)
    rows = frappe.get_all(
        "Animal",
        filters=_animal_filters(company_id, herd_batch, warehouse, category, status, search),
        fields=ANIMAL_FIELDS,
        order_by="modified desc",
        limit_start=(page - 1) * limit,
        limit_page_length=limit,
    )
    return [_map_animal(row) for row in rows]


@frappe.whitelist()
def get_animal(company_id, animal_id):
    row = _load_animal(company_id, animal_id)
    if not row:
        return None
    return _map_animal(row)


@frappe.whitelist()
def get_animal_by_ear_tag(company_id, ear_tag_id):
    rows = frappe.get_all(
        "Animal",
        filters=[["company", "=", company_id], ["ear_tag_id", "=", ear_tag_id]],
        fields=ANIMAL_FIELDS,
        limit_page_length=1,
    )
    if not rows:
        return None
    return _map_animal(rows[0])


@frappe.whitelist()
def create_animal(company_id, ear_tag_id, species, current_category, sex="Desconocido", breed=None, birth_date=None, current_herd_batch=None, warehouse=None, origin_type=None, origin_document=None):
    doc = frappe.get_doc(
        {
            "doctype": "Animal",
            "company": company_id,
            "ear_tag_id": ear_tag_id,
            "species": species,
            "sex": sex or "Desconocido",
            "current_category": current_category,
            "breed": breed,
            "birth_date": birth_date,
            "current_herd_batch": current_herd_batch,
            "warehouse": warehouse,
            "origin_type": origin_type or "Other",
            "origin_document": origin_document,
        }
    )
    doc.insert()
    return _map_animal(doc.as_dict())


@frappe.whitelist()
def update_animal(company_id, animal_id, ear_tag_id=None, species=None, sex=None, current_category=None, breed=None, birth_date=None, current_weight=None, current_herd_batch=None, warehouse=None, origin_type=None, origin_document=None):
    row = _load_animal(company_id, animal_id)
    if not row:
        return None

    doc = frappe.get_doc("Animal", animal_id)
    if ear_tag_id is not None:
        doc.ear_tag_id = ear_tag_id
    if species is not None:
        doc.species = species
    if sex is not None:
        doc.sex = sex
    if current_category is not None:
        doc.current_category = current_category
    if breed is not None:
        doc.breed = breed
    if birth_date is not None:
        doc.birth_date = birth_date
    if current_weight is not None:
        doc.current_weight = current_weight
    if current_herd_batch is not None:
        doc.current_herd_batch = current_herd_batch
    if warehouse is not None:
        doc.warehouse = warehouse
    if origin_type is not None:
        doc.origin_type = origin_type
    if origin_document is not None:
        doc.origin_document = origin_document

    doc.save()
    return _map_animal(doc.as_dict())


@frappe.whitelist()
def delete_animal(company_id, animal_id):
    row = _load_animal(company_id, animal_id)
    if not row:
        return False

    doc = frappe.get_doc("Animal", animal_id)
    doc.disabled = 1
    doc.save()
    return True


@frappe.whitelist()
def move_animal(company_id, animal_id, corral_id, event_date=None, notes=None):
    if not _load_corral(company_id, corral_id):
        return {"ok": False, "error": "INVALID_CORRAL"}

    row = _load_animal(company_id, animal_id)
    if not row:
        return {"ok": False, "error": "ANIMAL_NOT_FOUND"}

    return _move_animal_record(company_id, row, corral_id, event_date=event_date, notes=notes)


@frappe.whitelist()
def bulk_move_animals(company_id, animal_ids, target_corral_id, event_date=None, notes=None):
    parsed_ids = _parse_animal_ids(animal_ids)
    if len(parsed_ids) > BULK_MOVE_MAX:
        return {"ok": False, "error": "BATCH_TOO_LARGE"}

    if not _load_corral(company_id, target_corral_id):
        return {"ok": False, "error": "INVALID_CORRAL"}

    animals = frappe.get_all(
        "Animal",
        filters=[
            ["company", "=", company_id],
            ["name", "in", parsed_ids],
            ["disabled", "=", 0],
        ],
        fields=["name", "ear_tag_id", "warehouse", "current_herd_batch"],
        limit_page_length=BULK_MOVE_MAX,
    )

    result = {"movedCount": 0, "skippedCount": 0, "failed": []}

    for row in animals:
        try:
            outcome = _move_animal_record(
                company_id,
                row,
                target_corral_id,
                event_date=event_date,
                notes=notes,
            )
            if not outcome.get("ok"):
                if outcome.get("error") == "SAME_CORRAL":
                    result["skippedCount"] += 1
                continue

            result["movedCount"] += 1
        except Exception as err:
            animal_id = str(row.get("name") or "")
            frappe.log_error(frappe.get_traceback(), f"Animal bulk move failed: {animal_id}")
            result["failed"].append(
                {
                    "animalId": animal_id,
                    "earTagId": str(row.get("ear_tag_id") or ""),
                    "reason": str(err),
                }
            )

    return {"ok": True, "result": result}


@frappe.whitelist()
def assign_ear_tag(company_id, animal_id, ear_tag_id):
    new_eid = str(ear_tag_id or "").strip()
    if not new_eid:
        return {"ok": False, "error": "INVALID_EID"}

    row = _load_animal(company_id, animal_id)
    if not row:
        return {"ok": False, "error": "ANIMAL_NOT_FOUND"}

    current_eid = str(row.get("ear_tag_id") or "")
    if not current_eid.startswith(PLACEHOLDER_PREFIX):
        return {"ok": False, "error": "NOT_A_PLACEHOLDER"}

    collision = frappe.get_all(
        "Animal",
        filters=[
            ["company", "=", company_id],
            ["ear_tag_id", "=", new_eid],
            ["disabled", "=", 0],
            ["name", "!=", animal_id],
        ],
        fields=["name"],
        limit_page_length=1,
    )
    if collision:
        return {"ok": False, "error": "EID_ALREADY_IN_USE"}

    animal = frappe.get_doc("Animal", animal_id)
    animal.ear_tag_id = new_eid
    animal.save()

    _create_animal_event(
        animal_id,
        "Marca",
        notes=f"Caravana asignada: {current_eid} → {new_eid}",
    )

    data = _map_animal(animal.as_dict())
    data["isPlaceholder"] = False
    return {"ok": True, "data": data}
