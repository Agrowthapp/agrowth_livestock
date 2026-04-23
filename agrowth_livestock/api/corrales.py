import frappe
from frappe.utils import cint, now

VALID_CORRAL_TYPES = ["Acostumbramiento", "Sanidad", "Operativo"]


def _map_corral(row, head_count=0):
    return {
        "id": str(row.get("name") or ""),
        "name": str(row.get("warehouse_name") or row.get("name") or ""),
        "company": str(row.get("company") or ""),
        "corralType": row.get("corral_type") or "Operativo",
        "headCount": cint(head_count or 0),
        "fieldId": str(row.get("agro_field") or ""),
    }


def _load_corral(company_id, corral_id):
    rows = frappe.get_all(
        "Warehouse",
        filters=[
            ["name", "=", corral_id],
            ["company", "=", company_id],
            ["is_corral", "=", 1],
        ],
        fields=["name", "warehouse_name", "company", "corral_type", "disabled", "is_group", "agro_field"],
        limit_page_length=1,
    )
    return rows[0] if rows else None


def _load_herd_batch(company_id, herd_batch_id):
    rows = frappe.get_all(
        "Herd Batch",
        filters=[
            ["company", "=", company_id],
            ["name", "=", herd_batch_id],
        ],
        fields=["name", "warehouse"],
        limit_page_length=1,
    )
    return rows[0] if rows else None


@frappe.whitelist()
def validate_corral(company_id, corral_id):
    row = _load_corral(company_id, corral_id)
    return bool(row and cint(row.get("disabled") or 0) == 0 and cint(row.get("is_group") or 0) == 0)


@frappe.whitelist()
def list_corrales(company_id, page=1, page_size=50, search=None):
    page = max(cint(page), 1)
    page_size = min(max(cint(page_size), 1), 200)

    filters = [
        ["company", "=", company_id],
        ["is_corral", "=", 1],
        ["disabled", "=", 0],
        ["is_group", "=", 0],
    ]
    if search:
        filters.append(["warehouse_name", "like", f"%{search}%"])

    corrales = frappe.get_all(
        "Warehouse",
        filters=filters,
        fields=["name", "warehouse_name", "company", "corral_type", "is_corral", "agro_field"],
        limit_start=(page - 1) * page_size,
        limit_page_length=page_size,
        order_by="modified desc",
    )

    if not corrales:
        return []

    corral_ids = [str(row.get("name") or "") for row in corrales]
    animals = frappe.get_all(
        "Animal",
        filters=[
            ["company", "=", company_id],
            ["warehouse", "in", corral_ids],
            ["disabled", "=", 0],
        ],
        fields=["name", "warehouse"],
        limit_page_length=10000,
    )

    head_count_map = {}
    for animal in animals:
        warehouse = str(animal.get("warehouse") or "")
        head_count_map[warehouse] = head_count_map.get(warehouse, 0) + 1

    return [_map_corral(row, head_count_map.get(str(row.get("name") or ""), 0)) for row in corrales]


@frappe.whitelist()
def create_corral(company_id, name, corral_type, agro_field=None):
    if not name or not str(name).strip():
        frappe.throw("name es requerido")
    if corral_type not in VALID_CORRAL_TYPES:
        frappe.throw("corralType inválido")
    if not agro_field:
        frappe.throw("field_id es requerido")

    parent_warehouse = frappe.db.get_value("Agro Field", agro_field, "warehouse_id") or ""

    doc = frappe.get_doc(
        {
            "doctype": "Warehouse",
            "warehouse_name": str(name).strip(),
            "company": company_id,
            "is_corral": 1,
            "corral_type": corral_type,
            "is_group": 0,
            "agro_field": agro_field,
            "parent_warehouse": parent_warehouse,
        }
    )
    doc.insert()
    return _map_corral(doc.as_dict(), 0)


@frappe.whitelist()
def update_corral(company_id, corral_id, name=None, corral_type=None, agro_field=None):
    row = _load_corral(company_id, corral_id)
    if not row or cint(row.get("disabled") or 0) == 1:
        return None

    if corral_type is not None and corral_type not in VALID_CORRAL_TYPES:
        frappe.throw("corralType inválido")

    doc = frappe.get_doc("Warehouse", corral_id)
    if name is not None:
        doc.warehouse_name = str(name).strip()
    if corral_type is not None:
        doc.corral_type = corral_type
    if agro_field is not None:
        doc.agro_field = agro_field
    doc.save()
    return _map_corral(doc.as_dict(), 0)


@frappe.whitelist()
def disable_corral(company_id, corral_id):
    row = _load_corral(company_id, corral_id)
    if not row:
        return {"ok": False, "error": "NOT_FOUND"}

    animals = frappe.get_all(
        "Animal",
        filters=[
            ["company", "=", company_id],
            ["warehouse", "=", corral_id],
            ["disabled", "=", 0],
        ],
        fields=["name"],
        limit_page_length=1,
    )
    if animals:
        return {"ok": False, "error": "CORRAL_HAS_ANIMALS"}

    doc = frappe.get_doc("Warehouse", corral_id)
    doc.disabled = 1
    doc.save()
    return {"ok": True}


@frappe.whitelist()
def assign_tropa_to_corral(company_id, herd_batch_id, target_corral_id, event_date=None, notes=None):
    corral = _load_corral(company_id, target_corral_id)
    if not corral or cint(corral.get("disabled") or 0) == 1 or cint(corral.get("is_group") or 0) == 1:
        frappe.throw("INVALID_CORRAL")

    batch = _load_herd_batch(company_id, herd_batch_id)
    if not batch:
        frappe.throw("Herd Batch no encontrada")

    current_batch_warehouse = str(batch.get("warehouse") or "")
    if current_batch_warehouse != target_corral_id:
        batch_doc = frappe.get_doc("Herd Batch", herd_batch_id)
        batch_doc.warehouse = target_corral_id
        batch_doc.save()

    animals = frappe.get_all(
        "Animal",
        filters=[
            ["company", "=", company_id],
            ["current_herd_batch", "=", herd_batch_id],
            ["disabled", "=", 0],
        ],
        fields=["name", "ear_tag_id", "warehouse", "current_herd_batch"],
        limit_page_length=500,
    )

    result = {"movedCount": 0, "failed": []}
    effective_event_date = event_date or now()

    for animal in animals:
        animal_id = str(animal.get("name") or "")
        ear_tag_id = str(animal.get("ear_tag_id") or "")
        try:
            animal_doc = frappe.get_doc("Animal", animal_id)
            animal_doc.warehouse = target_corral_id
            animal_doc.save()

            frappe.get_doc(
                {
                    "doctype": "Animal Event",
                    "animal": animal_id,
                    "event_type": "Movimiento",
                    "event_date": effective_event_date,
                    "new_warehouse": target_corral_id,
                    "notes": notes or f"Asignado a corral {target_corral_id}",
                }
            ).insert()

            result["movedCount"] += 1
        except Exception as exc:
            result["failed"].append(
                {
                    "animalId": animal_id,
                    "earTagId": ear_tag_id,
                    "reason": str(exc),
                }
            )

    return result
