import frappe
from frappe.utils import cint

GROUPED_FETCH_CAP = 1000


def _ensure_dict(value):
    if isinstance(value, str):
        try:
            parsed = frappe.parse_json(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def _ensure_list(value):
    if isinstance(value, str):
        try:
            parsed = frappe.parse_json(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return value if isinstance(value, list) else []


def _normalize_text(value):
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def _normalize_eid_list(eid_list):
    values = _ensure_list(eid_list)
    seen = set()
    normalized = []
    for value in values:
        trimmed = _normalize_text(str(value) if value is not None else None)
        if not trimmed:
            continue
        key = trimmed.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(trimmed)
    return normalized


def _as_number(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except Exception:
            return None
    return None


def _normalize_treatments(params):
    if params.get("eventType") != "Sanidad":
        return []

    treatments = _ensure_list(params.get("treatments"))
    normalized = []
    if treatments:
        for item in treatments:
            row = _ensure_dict(item)
            treatment = _normalize_text(row.get("treatment")) or ""
            if not treatment:
                continue
            normalized.append(
                {
                    "treatment": treatment,
                    "drug": _normalize_text(row.get("drug")),
                    "dose": _normalize_text(row.get("dose")),
                    "reason": _normalize_text(row.get("reason")),
                    "notes": _normalize_text(row.get("notes")),
                }
            )
        return normalized

    legacy_treatment = _normalize_text(params.get("treatment"))
    if not legacy_treatment:
        return []

    return [{"treatment": legacy_treatment, "notes": _normalize_text(params.get("notes"))}]


ANIMAL_FIELDS = [
    "name",
    "ear_tag_id",
    "current_weight",
    "current_herd_batch",
    "warehouse",
]

# BUG 3 fix: EVENT_FIELDS is the canonical "what to fetch from Animal
# Event" list. The full set is the union of base doctype fields,
# in-flight schema additions, and custom fields. Some of these fields
# (e.g. `ear_tag_id`, `line_index`, `event_group_id`, `scope_type`,
# `scope_ref`, `unidentified_head_count`, `identification_status`) are
# NOT declared on the base Animal Event doctype and are not yet
# installed as Custom Fields on the running site. Passing the raw
# constant to `frappe.get_all` would build a `SELECT *` that includes
# a missing column, and MySQL would return
# `1054 Unknown column 'X' in 'SELECT'` (which Frappe maps to a 500
# on the API surface). The fix is `_existing_event_fields()`: a helper
# that filters the constant to only fields that exist on the doctype,
# so the SQL never requests a missing column. Sites that have
# installed the custom fields get the full set; sites that have not
# get a defensive subset (and resolve `ear_tag_id` from the linked
# Animal row in `_resolve_animal_ear_tags`).
EVENT_FIELDS = [
    "name",
    "animal",
    "ear_tag_id",
    "event_type",
    "event_date",
    "treatment",
    "drug",
    "dose",
    "reason",
    "line_index",
    "new_weight",
    "new_warehouse",
    "notes",
    "event_group_id",
    "scope_type",
    "scope_ref",
    "unidentified_head_count",
    "identification_status",
]

# Frappe standard fields that are always present and should not be
# filtered out by `_existing_event_fields` even though
# `get_meta().get_field` would still find them via the parent
# Document class.
STANDARD_EVENT_FIELDS = {
    "name",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "idx",
    "parent",
    "parentfield",
    "parenttype",
}


def _existing_event_fields():
    """Return the subset of EVENT_FIELDS that exist on the running
    Animal Event doctype (base + custom fields). Defensive — sites
    without the custom fields installed still get a working query."""
    if not getattr(frappe, "db", None) or not getattr(frappe, "get_meta", None):
        # Bench smoke / unit-test path: return the base schema we know
        # is on Animal Event (verified against animal_event.json) so the
        # helper does not crash on import.
        return [
            "name",
            "animal",
            "event_type",
            "event_date",
            "new_weight",
            "new_warehouse",
            "treatment",
            "notes",
        ]
    try:
        meta = frappe.get_meta("Animal Event")
    except Exception:
        return [
            "name",
            "animal",
            "event_type",
            "event_date",
            "new_weight",
            "new_warehouse",
            "treatment",
            "notes",
        ]
    return [field for field in EVENT_FIELDS if field in STANDARD_EVENT_FIELDS or meta.get_field(field)]


def _has_event_field(fieldname):
    """True when the running Animal Event doctype declares the field
    (base + custom fields). Used by the call sites to decide whether
    to resolve `ear_tag_id` from the linked Animal row."""
    if not getattr(frappe, "db", None) or not getattr(frappe, "get_meta", None):
        return False
    try:
        meta = frappe.get_meta("Animal Event")
    except Exception:
        return False
    return fieldname in STANDARD_EVENT_FIELDS or bool(meta.get_field(fieldname))


def _resolve_animal_ear_tags(animal_ids):
    """Return a `{animal_id: ear_tag_id}` map for the given set of
    Animal names. Used by the labore history endpoints to populate
    `earTagId` on the response when the `Animal Event.ear_tag_id`
    column is not present (BUG 3). The lookup is a single
    `frappe.get_all` regardless of input size."""
    if not animal_ids:
        return {}
    rows = frappe.get_all(
        "Animal",
        filters=[["name", "in", list(animal_ids)]],
        fields=["name", "ear_tag_id"],
        limit_page_length=max(len(animal_ids), 1),
    )
    return {str(r.get("name") or ""): str(r.get("ear_tag_id") or "") for r in rows}


def _list_animals(company_id, filters=None):
    effective_filters = [["company", "=", company_id], ["disabled", "=", 0]]
    if filters:
        effective_filters.extend(filters)
    return frappe.get_all(
        "Animal",
        filters=effective_filters,
        fields=ANIMAL_FIELDS,
        limit_page_length=GROUPED_FETCH_CAP,
    )


def _resolve_animals_in_scope(company_id, scope_type, scope_id):
    filters = []
    if scope_type == "corral":
        filters.append(["warehouse", "=", scope_id])
    elif scope_type == "herd_batch":
        filters.append(["current_herd_batch", "=", scope_id])
    else:
        filters.append(["name", "=", scope_id])
    return _list_animals(company_id, filters)


def _resolve_animals_for_params(company_id, params):
    requested_eids = _normalize_eid_list(params.get("eidList"))
    scope_type = params.get("scopeType")

    if scope_type == "partial_unidentified":
        return {"animals": [], "invalidEids": []}

    if scope_type == "animal" and requested_eids:
        animals = _list_animals(company_id)
        eid_set = {eid.lower() for eid in requested_eids}
        matched = [
            animal
            for animal in animals
            if _normalize_text(animal.get("ear_tag_id"))
            and str(animal.get("ear_tag_id")).lower() in eid_set
        ]
        matched_eids = {
            str(animal.get("ear_tag_id") or "").lower()
            for animal in matched
            if animal.get("ear_tag_id")
        }
        return {
            "animals": matched,
            "invalidEids": [eid for eid in requested_eids if eid.lower() not in matched_eids],
        }

    scope_id = _normalize_text(params.get("scopeId"))
    if not scope_id:
        return {"animals": [], "invalidEids": requested_eids}

    animals = _resolve_animals_in_scope(company_id, scope_type, scope_id)
    if requested_eids:
        eid_set = {eid.lower() for eid in requested_eids}
        animals = [
            animal
            for animal in animals
            if _normalize_text(animal.get("ear_tag_id"))
            and str(animal.get("ear_tag_id")).lower() in eid_set
        ]
        matched_eids = {
            str(animal.get("ear_tag_id") or "").lower()
            for animal in animals
            if animal.get("ear_tag_id")
        }
        return {
            "animals": animals,
            "invalidEids": [eid for eid in requested_eids if eid.lower() not in matched_eids],
        }

    return {"animals": animals, "invalidEids": []}


def _build_event_payload(animal, params, event_group_id, line_index, treatment=None):
    payload = {
        "doctype": "Animal Event",
        "event_type": params.get("eventType"),
        "event_date": params.get("eventDate"),
        "event_group_id": event_group_id,
        "line_index": line_index,
        "scope_type": params.get("scopeType"),
        "identification_status": "pending_identification" if params.get("scopeType") == "partial_unidentified" else "identified",
    }

    scope_ref = _normalize_text(params.get("scopeId"))
    if scope_ref:
        payload["scope_ref"] = scope_ref
    if animal and animal.get("name"):
        payload["animal"] = animal.get("name")
    if animal and animal.get("ear_tag_id"):
        payload["ear_tag_id"] = animal.get("ear_tag_id")

    if params.get("scopeType") == "partial_unidentified":
        payload["unidentified_head_count"] = params.get("unidentifiedHeadCount") or 0

    if params.get("eventType") == "Pesada":
        payload["new_weight"] = params.get("defaultWeight")
        if params.get("notes"):
            payload["notes"] = params.get("notes")
        return payload

    if params.get("eventType") == "Sanidad":
        if treatment:
            payload["treatment"] = treatment.get("treatment")
            if treatment.get("drug"):
                payload["drug"] = treatment.get("drug")
            if treatment.get("dose"):
                payload["dose"] = treatment.get("dose")
            if treatment.get("reason"):
                payload["reason"] = treatment.get("reason")
            payload["notes"] = treatment.get("notes") or params.get("notes")
        elif params.get("treatment"):
            payload["treatment"] = params.get("treatment")
            if params.get("notes"):
                payload["notes"] = params.get("notes")
        return payload

    payload["notes"] = params.get("notes") or "Marca aplicada"
    return payload


def _apply_labor_to_animal(animal, params, event_group_id, treatments):
    if params.get("eventType") == "Pesada" and params.get("defaultWeight") is not None:
        animal_doc = frappe.get_doc("Animal", animal.get("name"))
        animal_doc.current_weight = params.get("defaultWeight")
        animal_doc.save()

    if params.get("eventType") == "Sanidad":
        payloads = [
            _build_event_payload(animal, params, event_group_id, index, treatment)
            for index, treatment in enumerate(treatments)
        ]
    else:
        payloads = [_build_event_payload(animal, params, event_group_id, 0)]

    for payload in payloads:
        frappe.get_doc(payload).insert()


def _create_partial_unidentified_operation(params, event_group_id, treatments):
    payloads = [
        _build_event_payload(None, params, event_group_id, index, treatment)
        for index, treatment in enumerate(treatments)
    ]
    for payload in payloads:
        frappe.get_doc(payload).insert()


def _map_history_row(raw, ear_tag_map=None):
    row_id = str(raw.get("name") or "")
    # BUG 3 fix: when the event row did not carry `ear_tag_id` (because
    # the column is not on the doctype), fall back to the per-batch
    # Animal lookup the caller provided. This keeps the BFF DTO
    # `LaborHistoryRow.earTagId` populated without requiring a Frappe
    # migration or a BFF retry pattern.
    ear_tag_id = _normalize_text(raw.get("ear_tag_id"))
    if not ear_tag_id and ear_tag_map:
        animal_id = str(raw.get("animal") or "")
        ear_tag_id = ear_tag_map.get(animal_id) or None
    return {
        "id": row_id,
        "groupId": _normalize_text(raw.get("event_group_id")) or row_id,
        "animalId": _normalize_text(raw.get("animal")) or "",
        "earTagId": ear_tag_id,
        "eventType": _normalize_text(raw.get("event_type")) or "",
        "eventDate": _normalize_text(raw.get("event_date")) or "",
        "treatment": _normalize_text(raw.get("treatment")),
        "drug": _normalize_text(raw.get("drug")),
        "dose": _normalize_text(raw.get("dose")),
        "reason": _normalize_text(raw.get("reason")),
        "lineIndex": _as_number(raw.get("line_index")),
        "newWeight": _as_number(raw.get("new_weight")),
        "newWarehouse": _normalize_text(raw.get("new_warehouse")),
        "notes": _normalize_text(raw.get("notes")),
        "scopeType": raw.get("scope_type"),
        "scopeRef": _normalize_text(raw.get("scope_ref")),
        "unidentifiedHeadCount": _as_number(raw.get("unidentified_head_count")),
        "identificationStatus": raw.get("identification_status"),
    }


def _build_event_filters(event_type=None, animal_id=None, scope_type=None, scope_id=None, from_date=None, to_date=None):
    filters = []
    if event_type:
        filters.append(["event_type", "=", event_type])
    if animal_id:
        filters.append(["animal", "=", animal_id])
    if scope_type:
        filters.append(["scope_type", "=", scope_type])
    if scope_id:
        filters.append(["scope_ref", "=", scope_id])
    if from_date:
        filters.append(["event_date", ">=", from_date])
    if to_date:
        filters.append(["event_date", "<=", to_date])
    return filters or None


def _build_summary(rows):
    first = rows[0] if rows else None
    if not first:
        return "Sin detalle"
    if first.get("eventType") == "Pesada":
        return f"{first.get('newWeight')} kg" if first.get("newWeight") is not None else first.get("notes") or "Pesada registrada"
    if first.get("eventType") == "Sanidad":
        descriptors = []
        seen = set()
        for row in rows:
            label = row.get("drug") or row.get("treatment") or row.get("reason")
            if label and label not in seen:
                seen.add(label)
                descriptors.append(label)
            if len(descriptors) >= 2:
                break
        if descriptors:
            return " + ".join(descriptors)
        return first.get("notes") or "Sanidad registrada"
    return first.get("notes") or first.get("eventType") or ""


def _group_history_rows(rows):
    groups = {}
    for row in rows:
        key = row.get("groupId") or row.get("id")
        groups.setdefault(key, []).append(row)

    summaries = []
    for group_id, group_rows in groups.items():
        sorted_rows = sorted(
            group_rows,
            key=lambda row: (row.get("eventDate") or "", row.get("lineIndex") or 0),
            reverse=True,
        )
        first = sorted_rows[0] if sorted_rows else {}
        animal_ids = {row.get("animalId") for row in sorted_rows if row.get("animalId")}
        line_keys = {
            f"{row.get('lineIndex') or 0}|{row.get('treatment') or ''}|{row.get('drug') or ''}|{row.get('dose') or ''}|{row.get('reason') or ''}"
            for row in sorted_rows
        }
        summaries.append(
            {
                "groupId": group_id,
                "eventType": first.get("eventType") or "Sanidad",
                "eventDate": first.get("eventDate") or "",
                "scopeType": first.get("scopeType"),
                "scopeRef": first.get("scopeRef"),
                "animalCount": len(animal_ids),
                "unidentifiedHeadCount": max([row.get("unidentifiedHeadCount") or 0 for row in sorted_rows]) or None,
                "identificationStatus": first.get("identificationStatus"),
                "lineCount": len(line_keys),
                "summary": _build_summary(sorted_rows),
            }
        )

    return sorted(summaries, key=lambda row: row.get("eventDate") or "", reverse=True)


def _build_group_detail(rows, group_id):
    sorted_rows = sorted(
        rows,
        key=lambda row: (row.get("eventDate") or "", row.get("lineIndex") or 0),
        reverse=True,
    )
    first = sorted_rows[0] if sorted_rows else {}
    treatments = []
    treatment_seen = set()
    animals = []
    animal_seen = set()

    for row in sorted_rows:
        if row.get("eventType") == "Sanidad" and row.get("treatment"):
            key = f"{row.get('lineIndex') or 0}|{row.get('treatment')}|{row.get('drug') or ''}|{row.get('dose') or ''}|{row.get('reason') or ''}"
            if key not in treatment_seen:
                treatment_seen.add(key)
                treatments.append(
                    {
                        "treatment": row.get("treatment"),
                        "drug": row.get("drug"),
                        "dose": row.get("dose"),
                        "reason": row.get("reason"),
                        "notes": row.get("notes"),
                    }
                )
        if row.get("animalId") and row.get("animalId") not in animal_seen:
            animal_seen.add(row.get("animalId"))
            animals.append({"animalId": row.get("animalId"), "earTagId": row.get("earTagId")})

    return {
        "groupId": group_id,
        "eventType": first.get("eventType") or "Sanidad",
        "eventDate": first.get("eventDate") or "",
        "scopeType": first.get("scopeType"),
        "scopeRef": first.get("scopeRef"),
        "notes": first.get("notes"),
        "unidentifiedHeadCount": max([row.get("unidentifiedHeadCount") or 0 for row in sorted_rows]) or None,
        "identificationStatus": first.get("identificationStatus"),
        "treatments": treatments or None,
        "animals": animals,
    }


@frappe.whitelist()
def apply_bulk_labor(company_id, params):
    params = _ensure_dict(params)
    event_group_id = frappe.generate_hash(length=10)
    treatments = _normalize_treatments(params)

    if params.get("scopeType") == "partial_unidentified":
        head_count = int(_as_number(params.get("unidentifiedHeadCount")) or 0)
        _create_partial_unidentified_operation(params, event_group_id, treatments)
        return {
            "ok": True,
            "result": {
                "requested": head_count,
                "succeeded": head_count,
                "failed": [],
                "invalidEids": [],
                "groupId": event_group_id,
                "unidentifiedHeadCount": head_count,
                "identificationStatus": "pending_identification",
            },
        }

    resolved = _resolve_animals_for_params(company_id, params)
    animals = resolved.get("animals") or []
    invalid_eids = resolved.get("invalidEids") or []
    if not animals:
        return {"ok": False, "error": "NO_ANIMALS_IN_SCOPE"}

    result = {
        "requested": len(animals),
        "succeeded": 0,
        "failed": [],
        "invalidEids": invalid_eids,
        "groupId": event_group_id,
        "identificationStatus": "identified",
    }

    for animal in animals:
        try:
            _apply_labor_to_animal(animal, params, event_group_id, treatments)
            result["succeeded"] += 1
        except Exception as exc:
            result["failed"].append(
                {
                    "animalId": str(animal.get("name") or ""),
                    "earTagId": str(animal.get("ear_tag_id") or ""),
                    "reason": str(exc),
                }
            )

    return {"ok": True, "result": result}


@frappe.whitelist()
def list_labores(company_id, event_type=None, animal_id=None, scope_type=None, scope_id=None, from_date=None, to_date=None, page=1, page_size=50):
    page = max(cint(page), 1)
    page_size = min(max(cint(page_size), 1), 100)
    # BUG 3 fix: filter EVENT_FIELDS to only fields that exist on the
    # running Animal Event doctype, so the SQL query never requests a
    # missing column (e.g. `ear_tag_id`).
    fields = _existing_event_fields()
    rows = frappe.get_all(
        "Animal Event",
        filters=_build_event_filters(event_type, animal_id, scope_type, scope_id, from_date, to_date),
        fields=fields,
        limit_page_length=page_size,
        limit_start=(page - 1) * page_size,
        order_by="event_date desc, modified desc",
    )
    # BUG 3 fix: when the `ear_tag_id` column is not on the event, fall
    # back to a single Animal lookup for the unique animal ids.
    ear_tag_map = (
        {}
        if _has_event_field("ear_tag_id")
        else _resolve_animal_ear_tags({str(r.get("animal") or "") for r in rows})
    )
    return [_map_history_row(row, ear_tag_map=ear_tag_map) for row in rows]


@frappe.whitelist()
def list_grouped_labores(company_id, event_type=None, animal_id=None, scope_type=None, scope_id=None, from_date=None, to_date=None, page=1, page_size=50):
    page = max(cint(page), 1)
    page_size = min(max(cint(page_size), 1), 100)
    # BUG 3 fix: filter EVENT_FIELDS to only fields that exist on the
    # running Animal Event doctype.
    fields = _existing_event_fields()
    rows = frappe.get_all(
        "Animal Event",
        filters=_build_event_filters(event_type, animal_id, scope_type, scope_id, from_date, to_date),
        fields=fields,
        limit_page_length=GROUPED_FETCH_CAP,
        limit_start=0,
        order_by="event_date desc, modified desc",
    )
    ear_tag_map = (
        {}
        if _has_event_field("ear_tag_id")
        else _resolve_animal_ear_tags({str(r.get("animal") or "") for r in rows})
    )
    grouped = _group_history_rows([_map_history_row(row, ear_tag_map=ear_tag_map) for row in rows])
    start = (page - 1) * page_size
    return {"rows": grouped[start:start + page_size], "total": len(grouped)}


@frappe.whitelist()
def get_labor_group_detail(company_id, group_id):
    # BUG 3 fix: filter EVENT_FIELDS to only fields that exist on the
    # running Animal Event doctype.
    fields = _existing_event_fields()
    group_rows = frappe.get_all(
        "Animal Event",
        filters=[["event_group_id", "=", group_id]],
        fields=fields,
        limit_page_length=GROUPED_FETCH_CAP,
        limit_start=0,
    )
    if not group_rows:
        group_rows = frappe.get_all(
            "Animal Event",
            filters=[["name", "=", group_id]],
            fields=fields,
            limit_page_length=GROUPED_FETCH_CAP,
            limit_start=0,
        )
    if not group_rows:
        return None
    ear_tag_map = (
        {}
        if _has_event_field("ear_tag_id")
        else _resolve_animal_ear_tags({str(r.get("animal") or "") for r in group_rows})
    )
    return _build_group_detail(
        [_map_history_row(row, ear_tag_map=ear_tag_map) for row in group_rows],
        group_id,
    )


@frappe.whitelist()
def apply_batch_labor(company_id, scopes, params):
    scopes = _ensure_list(scopes)
    params = _ensure_dict(params)
    results = []

    for scope in scopes:
        scope_row = _ensure_dict(scope)
        outcome = apply_bulk_labor(
            company_id,
            {
                **params,
                "scopeType": scope_row.get("scopeType"),
                "scopeId": scope_row.get("scopeId"),
            },
        )

        if not outcome.get("ok"):
            results.append(
                {
                    "scopeType": scope_row.get("scopeType"),
                    "scopeId": scope_row.get("scopeId"),
                    "requested": 0,
                    "succeeded": 0,
                    "failed": [],
                    "invalidEids": [],
                    "warning": outcome.get("error"),
                }
            )
            continue

        result = outcome.get("result") or {}
        results.append(
            {
                "scopeType": scope_row.get("scopeType"),
                "scopeId": scope_row.get("scopeId"),
                "requested": result.get("requested") or 0,
                "succeeded": result.get("succeeded") or 0,
                "failed": result.get("failed") or [],
                "invalidEids": result.get("invalidEids") or [],
                "groupId": result.get("groupId"),
            }
        )

    return {
        "totalRequested": sum(int(row.get("requested") or 0) for row in results),
        "totalSucceeded": sum(int(row.get("succeeded") or 0) for row in results),
        "invalidEids": [eid for row in results for eid in (row.get("invalidEids") or [])],
        "results": results,
    }
