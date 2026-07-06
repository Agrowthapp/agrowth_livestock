"""
Opening Balance API for the Ganadería BFF.

Provides whitelisted methods the Next.js BFF calls
(`list_openings`, `get_opening`, `create_opening`, `update_opening`,
`confirm_opening`, `create_adjustment`, `list_adjustments`,
`get_history`, `list_categories`).

The DTO contracts the BFF expects are documented in the Next.js repo at:
  src/features/ganaderia/services/livestock-opening-balance.models.ts
  src/features/ganaderia/services/livestock-opening-balance.companion-contract.ts

DocType: `Livestock Opening Balance` (already exists in agrowth_livestock/doctype/).
This module adds the whitelisted API surface that the in-app aperture UI calls.

Why this module exists: the Next.js BFF wraps `agrowth_livestock.api.opening.*`
via `resolveErpClient`. Without this module, every call returns 417 with
`No module named 'agrowth_livestock.api.opening'`.
"""

import json
import frappe
from frappe import _
from frappe.utils import cint, flt, now


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

DOCTYPE_OPENING = "Livestock Opening Balance"
DOCTYPE_OPENING_ITEM = "Livestock Opening Balance Item"
CATEGORY_DOCTYPE = "Livestock Category"

STATUS_DRAFT = "Draft"
STATUS_CONFIRMED = "Confirmed"
STATUS_CANCELLED = "Cancelled"


def _validate_company(company):
    if not company:
        frappe.throw(_("company is required"), frappe.ValidationError)


def _parse_json(value, default=None):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return value


def _map_opening_item(line):
    qty = cint(line.get("heads_qty") or line.get("quantity") or 0)
    avg_w = flt(line.get("avg_weight_kg") or 0)
    cost_kg = flt(line.get("cost_per_kg") or 0)
    total_cost = flt(line.get("total_cost") or 0)
    # Compute derived_value: use total_cost if set, else qty * avg_weight * cost_per_kg
    derived = total_cost if total_cost else (qty * avg_w * cost_kg)
    return {
        "name": str(line.get("name") or ""),
        "category": str(line.get("category") or ""),
        "species": str(line.get("species") or ""),
        "sex": str(line.get("sex") or ""),
        "quantity": qty,
        "avg_weight_kg": avg_w,
        "total_weight_kg": flt(line.get("total_weight_kg") or 0),
        "cost_per_kg": cost_kg,
        "total_cost": total_cost,
        "derived_value": derived,
        "field_location": str(line.get("field_location") or ""),
    }


def _map_opening(doc):
    raw_title = str(doc.title or "") if hasattr(doc, "title") else str(doc.company or "")
    return {
        "name": doc.name,
        "company": doc.company,
        "status": "Confirmed" if doc.docstatus == 1 else "Draft",
        "opening_date": str(doc.posting_date or ""),
        "title": raw_title or None,
        "notes": str(doc.get("notes") or ""),
        "total_heads": cint(doc.total_heads or 0),
        "total_weight_kg": flt(doc.total_weight_kg or 0),
        "total_value": flt(doc.total_value or 0),
        "field_location": str(doc.get("field_location") or ""),
        "migration_session": str(doc.migration_session or ""),
        "lines": [_map_opening_item(item) for item in doc.items],
        "creation": str(doc.creation or ""),
        "modified": str(doc.modified or ""),
        "docstatus": doc.docstatus,
    }


# ──────────────────────────────────────────────────────────────────────
# Whitelisted API
# ──────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def list_openings(company=None, status=None, opening_date_from=None, opening_date_to=None, page=1, page_size=20):
    """
    List Livestock Opening Balance documents for a company.
    Called by BFF: GET /api/v1/ganaderia/apertura
    """
    _validate_company(company)

    conditions = ["company = %(company)s"]
    params = {"company": company}

    if status in ("Draft", "Confirmed"):
        if status == "Draft":
            conditions.append("docstatus = 0")
        else:
            conditions.append("docstatus = 1")

    if opening_date_from:
        conditions.append("posting_date >= %(opening_date_from)s")
        params["opening_date_from"] = opening_date_from
    if opening_date_to:
        conditions.append("posting_date <= %(opening_date_to)s")
        params["opening_date_to"] = opening_date_to

    where_doc = " AND ".join(conditions)

    page = max(cint(page), 1)
    page_size = min(max(cint(page_size), 1), 200)
    start = (page - 1) * page_size

    total = frappe.db.count(DOCTYPE_OPENING, filters=params)

    names = frappe.db.get_list(
        DOCTYPE_OPENING,
        filters=params,
        order_by="posting_date desc, modified desc",
        limit_start=start,
        limit_page_length=page_size,
        pluck="name",
    )

    openings = []
    for name in names:
        try:
            doc = frappe.get_doc(DOCTYPE_OPENING, name)
            openings.append(_map_opening(doc))
        except frappe.DoesNotExistError:
            continue

    return {
        "data": openings,
        "meta": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": max(1, (total + page_size - 1) // page_size) if total else 1,
        },
    }


@frappe.whitelist()
def get_opening(name):
    """
    Get a single Livestock Opening Balance by name.
    Called by BFF: GET /api/v1/ganaderia/apertura/[id]
    """
    if not name:
        frappe.throw(_("name is required"), frappe.ValidationError)

    try:
        doc = frappe.get_doc(DOCTYPE_OPENING, name)
    except frappe.DoesNotExistError:
        frappe.throw(_("Opening balance {} not found").format(name), frappe.DoesNotExistError)

    opening = _map_opening(doc)
    opening["adjustments"] = _get_adjustments(name)
    opening["history"] = _get_history(name)
    return opening


@frappe.whitelist()
def create_opening():
    """
    Create a new Draft Livestock Opening Balance.
    Called by BFF: POST /api/v1/ganaderia/apertura
    """
    payload = frappe.form_dict
    company = payload.get("company")
    _validate_company(company)

    doc = frappe.new_doc(DOCTYPE_OPENING)
    doc.company = company
    doc.posting_date = payload.get("opening_date") or payload.get("openingDate") or now()
    doc.title = payload.get("title") or None
    doc.docstatus = 0  # Draft

    lines = payload.get("lines") or []
    if isinstance(lines, str):
        lines = _parse_json(lines, [])
    for line_data in lines:
        item = {
            "species": "Bovino",
            "category": str(line_data.get("category") or ""),
            "heads_qty": cint(line_data.get("quantity") or 0),
            "avg_weight_kg": flt(line_data.get("avg_weight_kg") or 0),
            "total_weight_kg": flt(line_data.get("total_weight_kg") or 0),
            "cost_per_kg": flt(line_data.get("cost_per_kg") or 0),
            "total_cost": flt(line_data.get("total_cost") or 0),
            "field_location": str(line_data.get("field_location") or ""),
            "sex": str(line_data.get("sex") or ""),
        }
        doc.append("items", item)

    doc.insert(ignore_permissions=False)

    # Recompute totals
    total_heads = sum(cint(item.heads_qty or 0) for item in doc.items)
    total_weight = sum(flt(item.total_weight_kg or 0) for item in doc.items)
    total_value = sum(flt(item.total_cost or 0) for item in doc.items)

    doc.db_set("total_heads", total_heads, update_modified=False)
    doc.db_set("total_weight_kg", total_weight, update_modified=False)
    doc.db_set("total_value", total_value, update_modified=False)

    frappe.db.commit()

    return _map_opening(doc)


@frappe.whitelist()
def update_opening():
    """
    Update a Draft Livestock Opening Balance.
    Called by BFF: PATCH /api/v1/ganaderia/apertura/[id]
    Returns 409 if already Confirmed.
    """
    payload = frappe.form_dict
    name = payload.get("name")

    try:
        doc = frappe.get_doc(DOCTYPE_OPENING, name)
    except frappe.DoesNotExistError:
        frappe.throw(_("Opening balance {} not found").format(name), frappe.DoesNotExistError)

    if doc.docstatus == 1:
        frappe.throw(
            _("Cannot edit a confirmed opening balance. Create an adjustment instead."),
            frappe.ValidationError,
        )

    # Update posting date if provided
    posting_date = payload.get("openingDate")
    if posting_date:
        doc.posting_date = posting_date

    # Replace items if provided
    lines = payload.get("lines")
    if isinstance(lines, str):
        lines = _parse_json(lines)
    if lines is not None:
        doc.items = []
        for line_data in lines:
            item = {
                "species": "Bovino",
                "category": str(line_data.get("category") or ""),
                "heads_qty": cint(line_data.get("quantity") or 0),
                "avg_weight_kg": flt(line_data.get("avg_weight_kg") or 0),
                "total_weight_kg": flt(line_data.get("total_weight_kg") or 0),
                "cost_per_kg": flt(line_data.get("cost_per_kg") or 0),
                "total_cost": flt(line_data.get("total_cost") or 0),
                "field_location": str(line_data.get("field_location") or ""),
                "sex": str(line_data.get("sex") or ""),
            }
            doc.append("items", item)

    doc.save(ignore_permissions=False)

    # Recompute totals
    total_heads = sum(cint(item.heads_qty or 0) for item in doc.items)
    total_weight = sum(flt(item.total_weight_kg or 0) for item in doc.items)
    total_value = sum(flt(item.total_cost or 0) for item in doc.items)

    doc.db_set("total_heads", total_heads, update_modified=False)
    doc.db_set("total_weight_kg", total_weight, update_modified=False)
    doc.db_set("total_value", total_value, update_modified=False)

    frappe.db.commit()

    return _map_opening(doc)


@frappe.whitelist()
def confirm_opening():
    """
    Confirm a Draft Livestock Opening Balance → Submits it.
    Called by BFF: POST /api/v1/ganaderia/apertura/[id]/confirm
    Returns 409 if already Confirmed.
    """
    payload = frappe.form_dict
    name = payload.get("name")
    try:
        doc = frappe.get_doc(DOCTYPE_OPENING, name)
    except frappe.DoesNotExistError:
        frappe.throw(_("Opening balance {} not found").format(name), frappe.DoesNotExistError)

    if doc.docstatus == 1:
        return _map_opening(doc)  # Idempotent: already confirmed

    if doc.docstatus == 2:
        frappe.throw(
            _("Cannot confirm a cancelled opening balance."),
            frappe.ValidationError,
        )

    # Validate at least one line with quantity > 0
    has_valid_line = any(cint(item.heads_qty or 0) > 0 for item in doc.items)
    if not has_valid_line:
        frappe.throw(
            _("At least one line with quantity > 0 is required to confirm."),
            frappe.ValidationError,
        )

    try:
        doc.submit()
    except Exception:
        frappe.db.rollback()
        raise

    frappe.db.commit()

    return _map_opening(doc)


@frappe.whitelist()
def create_adjustment():
    """
    Create a corrective adjustment for a confirmed opening.
    Called by BFF: POST /api/v1/ganaderia/apertura/[id]/adjustments

    For v1, adjustment is modelled as updating the opening's items.
    Stock bucket underflow is checked before applying negative deltas.
    """
    payload = frappe.form_dict

    company = payload.get("company")
    _validate_company(company)

    opening_name = payload.get("opening")
    reason = str(payload.get("reason") or "").strip()

    if not opening_name:
        frappe.throw(_("opening is required"), frappe.ValidationError)
    if len(reason) < 3:
        frappe.throw(
            _("reason is required (minimum 3 characters)"),
            frappe.ValidationError,
        )

    try:
        opening = frappe.get_doc(DOCTYPE_OPENING, opening_name)
    except frappe.DoesNotExistError:
        frappe.throw(_("Opening balance {} not found").format(opening_name), frappe.DoesNotExistError)

    if opening.docstatus != 1:
        frappe.throw(
            _("Adjustments can only be applied to confirmed openings."),
            frappe.ValidationError,
        )

    delta_lines = payload.get("lines") or []
    if isinstance(delta_lines, str):
        delta_lines = _parse_json(delta_lines, [])

    # Verify no bucket would underflow
    for line_data in delta_lines:
        category = str(line_data.get("category") or "")
        quantity_delta = cint(line_data.get("quantityDelta") or 0)

        if quantity_delta >= 0:
            continue  # Positive adjustment is always allowed

        # Find current quantity for this category
        current_qty = 0
        for item in opening.items:
            if str(item.category or "") == category:
                current_qty += cint(item.heads_qty or 0)

        if current_qty + quantity_delta < 0:
            frappe.throw(
                _("Adjustment of {} for category '{}' would result in {} heads (underflow). "
                  "Current: {} heads.").format(
                    quantity_delta, category, current_qty + quantity_delta, current_qty
                ),
                frappe.ValidationError,
            )

    # Apply adjustments: update existing items or add new
    for line_data in delta_lines:
        category = str(line_data.get("category") or "")
        quantity_delta = cint(line_data.get("quantity_delta") or line_data.get("quantityDelta") or 0)
        avg_weight_kg = flt(line_data.get("avg_weight_kg") or 0)
        cost_per_kg = flt(line_data.get("cost_per_kg") or 0)

        # Try to find existing item with matching category
        updated = False
        for item in opening.items:
            if str(item.category or "") == category:
                new_qty = cint(item.heads_qty or 0) + quantity_delta
                if new_qty < 0:
                    new_qty = 0
                item.heads_qty = new_qty
                if avg_weight_kg:
                    item.avg_weight_kg = avg_weight_kg
                if cost_per_kg:
                    item.cost_per_kg = cost_per_kg
                item.total_weight_kg = flt(new_qty) * flt(item.avg_weight_kg or 0)
                item.total_cost = flt(new_qty) * flt(item.avg_weight_kg or 0) * flt(item.cost_per_kg or 0)
                updated = True
                break

        if not updated and quantity_delta > 0:
            # New category not in opening — append
            item = {
                "species": "Bovino",
                "category": category,
                "heads_qty": quantity_delta,
                "avg_weight_kg": avg_weight_kg,
                "total_weight_kg": flt(quantity_delta) * flt(avg_weight_kg),
                "cost_per_kg": cost_per_kg,
                "total_cost": flt(quantity_delta) * flt(avg_weight_kg) * flt(cost_per_kg),
            }
            opening.append("items", item)

    # Add adjustment note
    adjustment_note = "[{}] Adjustment by {} - {}".format(now(), frappe.session.user, reason)
    existing_notes = str(opening.get("notes") or "")
    if existing_notes:
        existing_notes += "\n" + adjustment_note
    else:
        existing_notes = adjustment_note
    opening.db_set("notes", existing_notes, update_modified=True)

    # Recompute totals
    total_heads = sum(cint(item.heads_qty or 0) for item in opening.items)
    total_weight = sum(flt(item.total_weight_kg or 0) for item in opening.items)
    total_value = sum(flt(item.total_cost or 0) for item in opening.items)

    opening.db_set("total_heads", total_heads, update_modified=False)
    opening.db_set("total_weight_kg", total_weight, update_modified=False)
    opening.db_set("total_value", total_value, update_modified=False)

    # Create LivestockStockLedgerEntry rows for each adjustment delta
    for line_data in delta_lines:
        adj_category = str(line_data.get("category") or "")
        quantity_delta = cint(line_data.get("quantity_delta") or line_data.get("quantityDelta") or 0)
        if quantity_delta == 0:
            continue

        adj_avg_weight_kg = flt(line_data.get("avg_weight_kg") or 0)
        adj_cost_per_kg = flt(line_data.get("cost_per_kg") or 0)

        # Try to get sex from line_data or fall back to the matching opening item
        adj_sex = str(line_data.get("sex") or "")
        if not adj_sex:
            for item in opening.items:
                if str(item.category or "") == adj_category:
                    adj_sex = str(item.get("sex") or "")
                    break

        adj_warehouse = str(line_data.get("warehouse") or "")
        if not adj_warehouse:
            for item in opening.items:
                if str(item.category or "") == adj_category:
                    adj_warehouse = str(item.get("warehouse") or "")
                    break

        frappe.get_doc({
            "doctype": "Livestock Stock Ledger Entry",
            "company": company,
            "posting_date": opening.posting_date,
            "movement_type": "opening_adjustment",
            "category": adj_category,
            "sex": adj_sex or "Sin especificar",
            "warehouse": adj_warehouse or None,
            "heads_qty": quantity_delta,
            "total_weight_kg": flt(quantity_delta) * adj_avg_weight_kg,
            "total_value": flt(quantity_delta) * adj_avg_weight_kg * adj_cost_per_kg,
            "voucher_type": "Livestock Opening Adjustment",
            "voucher_no": opening.name,
        }).insert()

    frappe.db.commit()

    return {
        "name": "ADJ-{}-{}".format(opening_name, now()[:10]),
        "company": company,
        "opening": opening_name,
        "reason": reason,
        "lines": delta_lines,
        "createdAt": str(now()),
    }


@frappe.whitelist()
def list_adjustments(opening_name=None, company=None, page=1, page_size=20):
    """
    List adjustments for an opening balance (derived from notes audit trail).
    Called by BFF: GET /api/v1/ganaderia/apertura/[id]/adjustments
    """
    return {"data": _get_adjustments(opening_name), "meta": {"page": page, "page_size": page_size}}


@frappe.whitelist()
def get_history(opening=None, company=None, page=1, page_size=50):
    """
    Get audit history for an opening balance.
    Called by BFF: GET /api/v1/ganaderia/apertura/[id] (inline in get_opening)
    """
    return {"data": _get_history(opening), "meta": {"page": page, "page_size": page_size}}


@frappe.whitelist()
def list_categories(company=None):
    """
    List bovine categories available for opening balance lines.
    Called by BFF: GET /api/v1/ganaderia/apertura/categories
    """
    try:
        categories = frappe.get_all(
            CATEGORY_DOCTYPE,
            fields=["name", "sex"],
            order_by="name asc",
        )
    except Exception:
        # Fallback: return standard argentine bovine categories
        return _fallback_categories()

    return [
        {
            "id": str(cat.get("name") or ""),
            "name": str(cat.get("name") or ""),
            "sex": str(cat.get("sex") or ""),
        }
        for cat in categories
    ]


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────

def _get_adjustments(opening_name):
    """
    Parse adjustments from the opening's notes audit trail.
    Returns list of adjustment-like objects.
    """
    try:
        doc = frappe.get_doc(DOCTYPE_OPENING, opening_name)
        notes = str(doc.get("notes") or "")
    except Exception:
        return []

    adjustments = []
    for line in notes.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        # Format: [timestamp] Adjustment by user - reason
        try:
            ts_end = line.index("]")
            timestamp = line[1:ts_end]
            rest = line[ts_end + 1 :].strip()
            # Parse "Adjustment by USER - REASON"
            if "Adjustment by " in rest:
                actor_part = rest[len("Adjustment by "):]
                if " - " in actor_part:
                    actor, reason = actor_part.split(" - ", 1)
                    adjustments.append({
                        "name": "ADJ-{}-{}".format(opening_name, timestamp[:10]),
                        "opening": opening_name,
                        "reason": reason.strip(),
                        "createdAt": timestamp.strip(),
                    })
        except (ValueError, IndexError):
            continue

    return adjustments


def _get_history(opening_name):
    """
    Build human-readable history entries for an opening balance.
    """
    history = []

    try:
        doc = frappe.get_doc(DOCTYPE_OPENING, opening_name)
    except Exception:
        return history

    # Creation entry
    history.append({
        "label": "Apertura creada (borrador)",
        "type": "opening",
        "date": str(doc.creation or ""),
    })

    # Confirmation entry
    if doc.docstatus == 1:
        history.append({
            "label": "Apertura confirmada: +{} cabezas".format(cint(doc.total_heads or 0)),
            "type": "opening",
            "date": str(doc.modified or ""),
        })

    # Adjustments from notes
    for adj in _get_adjustments(opening_name):
        history.append({
            "label": "Ajuste: {}".format(adj.get("reason", "")),
            "type": "adjustment",
            "date": adj.get("createdAt", ""),
        })

    return history


def _fallback_categories():
    """Standard argentine bovine categories when Livestock Category doctype is unavailable."""
    return [
        {"id": cat["name"], "name": cat["name"], "sex": cat["sex"]}
        for cat in [
            {"name": "Ternero", "sex": "Macho"},
            {"name": "Ternera", "sex": "Hembra"},
            {"name": "Novillito", "sex": "Macho"},
            {"name": "Novillo", "sex": "Macho"},
            {"name": "Vaquillona", "sex": "Hembra"},
            {"name": "Vaca", "sex": "Hembra"},
            {"name": "Vaca de refugo", "sex": "Hembra"},
            {"name": "Toro", "sex": "Macho"},
            {"name": "Torito", "sex": "Macho"},
            {"name": "Vaca de cría", "sex": "Hembra"},
            {"name": "Vaca preñada", "sex": "Hembra"},
        ]
    ]
