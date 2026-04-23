import frappe
from frappe.utils import cint, flt

COMMERCIAL_LIQUIDATION_DOCTYPES = [
    "Livestock Sales Liquidation",
    "Livestock Settlement",
]
READY_EXIT_STATUSES = {"Listo para salir"}

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


def _dispatch_fields():
    return [
        "name",
        "company",
        "customer",
        "customer_name",
        "posting_date",
        "warehouse",
        "province",
        "withholding_profile",
        "ajuste_interno",
        "retencion_iibb_amount",
        "retencion_iigg_amount",
        "mode",
        "herd_batch",
        "livestock_settlement",
        "total_heads",
        "total_bruto",
        "total_iva",
        "total_retenciones",
        "total_neto",
        "sales_invoice",
        "stock_entry",
        "confirmation_status",
        "confirmation_mode",
        "confirmed_by",
        "confirmed_at",
        "docstatus",
        "items",
        "animals",
    ]


def _map_line(row):
    return {
        "id": str(row.get("name") or ""),
        "herdBatch": str(row.get("herd_batch") or ""),
        "itemCode": str(row.get("item_code") or ""),
        "category": str(row.get("category") or ""),
        "qtyHeads": cint(row.get("qty_heads") or 0),
        "avgWeight": flt(row.get("avg_weight")) if row.get("avg_weight") not in (None, "") else None,
        "unitPrice": flt(row.get("unit_price") or 0),
        "amount": flt(row.get("amount") or 0),
        "taxRate": flt(row.get("tax_rate") or 0),
        "taxAmount": flt(row.get("tax_amount") or 0),
    }


def _map_animal(row):
    return {
        "earTagId": str(row.get("ear_tag_id") or ""),
        "category": row.get("category") or None,
        "status": row.get("status") or None,
        "observation": row.get("observation") or None,
        "sourceCorral": row.get("source_corral") or None,
        "weight": flt(row.get("weight")) if row.get("weight") not in (None, "") else None,
        "isDuplicateInUpload": bool(row.get("is_duplicate_in_upload")),
        "notFoundInErp": bool(row.get("not_found_in_erp")),
    }


def _map_dispatch(doc):
    row = doc.as_dict() if callable(getattr(doc, "as_dict", None)) else doc
    lines = [_map_line(line) for line in (row.get("items") or [])]
    animals = [_map_animal(animal) for animal in (row.get("animals") or [])]
    total_heads_from_lines = sum(line["qtyHeads"] for line in lines)
    total_bruto = flt(row.get("total_bruto") or sum(line["amount"] for line in lines))
    total_iva = flt(row.get("total_iva") or sum(line["taxAmount"] for line in lines))

    return {
        "id": str(row.get("name") or ""),
        "name": str(row.get("name") or ""),
        "company": str(row.get("company") or ""),
        "customer": str(row.get("customer") or ""),
        "customerName": row.get("customer_name") or row.get("customer") or None,
        "postingDate": str(row.get("posting_date") or ""),
        "warehouse": str(row.get("warehouse") or ""),
        "province": str(row.get("province") or ""),
        "withholdingProfile": row.get("withholding_profile") or None,
        "iibbAmount": flt(row.get("retencion_iibb_amount") or 0),
        "iiggAmount": flt(row.get("retencion_iigg_amount") or 0),
        "internalAdjustment": flt(row.get("ajuste_interno") or 0),
        "mode": row.get("mode") or "Full Batch",
        "herdBatch": row.get("herd_batch") or None,
        "livestockSettlement": row.get("livestock_settlement") or None,
        "confirmationStatus": row.get("confirmation_status") or None,
        "confirmationMode": row.get("confirmation_mode") or None,
        "confirmedBy": row.get("confirmed_by") or None,
        "confirmedAt": str(row.get("confirmed_at")) if row.get("confirmed_at") else None,
        "totalHeads": cint(row.get("total_heads") or total_heads_from_lines or 0),
        "totalBruto": total_bruto,
        "totalIva": total_iva,
        "totalRetenciones": flt(row.get("total_retenciones") or 0),
        "totalNeto": flt(row.get("total_neto") or 0),
        "salesInvoice": row.get("sales_invoice") or None,
        "stockEntry": row.get("stock_entry") or None,
        "docstatus": cint(row.get("docstatus") or 0),
        "animals": animals,
        "lines": lines,
    }


def _parse_lines(lines):
    parsed = []
    for row in _parse_json(lines, []) or []:
        if not isinstance(row, dict):
            continue
        qty_heads = cint(row.get("qtyHeads") or row.get("qty_heads") or 0)
        unit_price = flt(row.get("unitPrice") or row.get("unit_price") or 0)
        tax_rate = flt(row.get("taxRate") or row.get("tax_rate") or 21)
        amount = qty_heads * unit_price
        parsed.append(
            {
                "herd_batch": row.get("herdBatch") or row.get("herd_batch") or None,
                "item_code": row.get("itemCode") or row.get("item_code"),
                "category": row.get("category"),
                "qty_heads": qty_heads,
                "avg_weight": row.get("avgWeight") or row.get("avg_weight") or None,
                "unit_price": unit_price,
                "amount": amount,
                "tax_rate": tax_rate,
                "tax_amount": amount * tax_rate / 100,
            }
        )
    return parsed


def _parse_animals(animals):
    parsed = []
    for row in _parse_json(animals, []) or []:
        if not isinstance(row, dict):
            continue
        parsed.append(
            {
                "ear_tag_id": row.get("ear_tag_id") or "",
                "category": row.get("category") or "Otro",
                "status": row.get("status") or "Listo para salir",
                "source_corral": row.get("source_corral") or "",
                "weight": row.get("weight") or 0,
                "observation": row.get("observation") or "",
            }
        )
    return parsed


def _load_dispatch(company_id, dispatch_id):
    if not frappe.db.exists("Livestock Dispatch", dispatch_id):
        return None
    doc = frappe.get_doc("Livestock Dispatch", dispatch_id)
    if str(doc.company or "") != str(company_id or ""):
        return None
    return doc


def _map_invoice_status(raw):
    outstanding = flt(raw.get("outstanding_amount") or 0)
    docstatus = cint(raw.get("docstatus") or 0)
    grand_total = flt(raw.get("grand_total") or 0)
    if docstatus == 2:
        return "cancelled"
    if outstanding <= 0:
        return "paid"
    if outstanding > 0 and outstanding < grand_total:
        return "partial"
    return "pending"


def _build_adjusted_items(doc, animals):
    ready_animals = [animal for animal in animals if str(animal.get("status") or "") in READY_EXIT_STATUSES]
    if not ready_animals or not doc.items:
        return None

    counts_by_category = {}
    for animal in ready_animals:
        key = str(animal.get("category") or "").strip() or "__uncategorized__"
        counts_by_category[key] = counts_by_category.get(key, 0) + 1

    next_items = []
    for index, line in enumerate(doc.items):
        category_key = str(line.category or "").strip() or "__uncategorized__"
        qty_heads = counts_by_category.get(category_key) or 0
        if qty_heads == 0 and len(doc.items) == 1 and index == 0:
            qty_heads = len(ready_animals)
        if qty_heads <= 0:
            continue
        unit_price = flt(line.unit_price or 0)
        tax_rate = flt(line.tax_rate or 21)
        amount = qty_heads * unit_price
        next_items.append(
            {
                "herd_batch": line.herd_batch,
                "item_code": line.item_code,
                "category": line.category,
                "qty_heads": qty_heads,
                "avg_weight": line.avg_weight or None,
                "unit_price": unit_price,
                "amount": amount,
                "tax_rate": tax_rate,
                "tax_amount": amount * tax_rate / 100,
            }
        )
    if not next_items:
        return None
    return {"total_heads": len(ready_animals), "items": next_items}


@frappe.whitelist()
def list_dispatches(company_id, customer=None, status=None, search=None, from_date=None, to_date=None, page=1, limit=200):
    page = max(cint(page), 1)
    limit = min(max(cint(limit), 1), 500)
    filters = [["company", "=", company_id]]
    if customer:
        filters.append(["customer", "=", customer])
    if status == "draft":
        filters.append(["docstatus", "=", 0])
    elif status == "submitted":
        filters.append(["docstatus", "=", 1])
    elif status == "cancelled":
        filters.append(["docstatus", "=", 2])
    if from_date:
        filters.append(["posting_date", ">=", from_date])
    if to_date:
        filters.append(["posting_date", "<=", to_date])
    if search:
        filters.append(["name", "like", f"%{search}%"])

    rows = frappe.get_all(
        "Livestock Dispatch",
        filters=filters,
        fields=_existing_fields("Livestock Dispatch", [field for field in _dispatch_fields() if field not in ("items", "animals")]),
        order_by="modified desc",
        limit_start=(page - 1) * limit,
        limit_page_length=limit,
    )
    return [_map_dispatch(row) for row in rows]


@frappe.whitelist()
def get_dispatch(company_id, dispatch_id):
    doc = _load_dispatch(company_id, dispatch_id)
    if not doc:
        return None
    return _map_dispatch(doc)


@frappe.whitelist()
def create_dispatch(company_id, posting_date, lines, customer=None, mode=None, herd_batch=None, livestock_settlement=None, warehouse=None, province=None, withholding_profile=None, iibb_amount=None, iigg_amount=None, internal_adjustment=None):
    parsed_lines = _parse_lines(lines)
    if not posting_date:
        frappe.throw("postingDate es requerido")
    if not parsed_lines:
        frappe.throw("Al menos una línea es requerida")

    resolved_mode = mode or ("Full Batch" if herd_batch else "Mixed")
    total_heads = sum(cint(line.get("qty_heads") or 0) for line in parsed_lines)
    doc = frappe.get_doc(
        {
            "doctype": "Livestock Dispatch",
            "company": company_id,
            "customer": customer,
            "posting_date": posting_date,
            "mode": resolved_mode,
            "herd_batch": herd_batch,
            "livestock_settlement": livestock_settlement,
            "warehouse": warehouse,
            "province": province,
            "withholding_profile": withholding_profile,
            "retencion_iibb_amount": iibb_amount,
            "retencion_iigg_amount": iigg_amount,
            "ajuste_interno": internal_adjustment,
            "total_heads": total_heads,
            "items": parsed_lines,
        }
    )
    doc.insert(ignore_permissions=True)
    return _map_dispatch(doc)


@frappe.whitelist()
def update_dispatch(company_id, dispatch_id, customer=None, posting_date=None, mode=None, herd_batch=None, livestock_settlement=None, warehouse=None, province=None, withholding_profile=None, iibb_amount=None, iigg_amount=None, internal_adjustment=None):
    doc = _load_dispatch(company_id, dispatch_id)
    if not doc:
        return None
    if cint(doc.docstatus or 0) != 0:
        frappe.throw("Solo se puede modificar un dispatch en estado draft")

    if customer is not None:
        doc.customer = customer
    if posting_date is not None:
        doc.posting_date = posting_date
    if mode is not None:
        doc.mode = mode
    if herd_batch is not None:
        doc.herd_batch = herd_batch
    if livestock_settlement is not None:
        doc.livestock_settlement = livestock_settlement
    if warehouse is not None:
        doc.warehouse = warehouse
    if province is not None:
        doc.province = province
    if withholding_profile is not None:
        doc.withholding_profile = withholding_profile
    if iibb_amount is not None:
        doc.retencion_iibb_amount = iibb_amount
    if iigg_amount is not None:
        doc.retencion_iigg_amount = iigg_amount
    if internal_adjustment is not None:
        doc.ajuste_interno = internal_adjustment

    doc.save(ignore_permissions=True)
    return _map_dispatch(doc)


@frappe.whitelist()
def submit_dispatch(company_id, dispatch_id):
    doc = _load_dispatch(company_id, dispatch_id)
    if not doc:
        return None
    if cint(doc.docstatus or 0) != 0:
        frappe.throw("Solo se puede submitear un dispatch en estado draft")
    doc.submit()
    return _map_dispatch(frappe.get_doc("Livestock Dispatch", dispatch_id))


@frappe.whitelist()
def cancel_dispatch(company_id, dispatch_id):
    doc = _load_dispatch(company_id, dispatch_id)
    if not doc:
        return None
    if cint(doc.docstatus or 0) != 1:
        frappe.throw("Solo se puede cancelar un dispatch submitado")
    doc.cancel()
    return _map_dispatch(frappe.get_doc("Livestock Dispatch", dispatch_id))


@frappe.whitelist()
def confirm_dispatch(company_id, dispatch_id, user, mode="None", animals=None):
    doc = _load_dispatch(company_id, dispatch_id)
    if not doc:
        return None
    if cint(doc.docstatus or 0) == 2:
        frappe.throw("No se puede confirmar un dispatch cancelado")

    parsed_animals = _parse_animals(animals)

    if parsed_animals and cint(doc.docstatus or 0) == 0:
        doc.set("animals", [])
        for row in parsed_animals:
            doc.append("animals", row)

        adjusted = _build_adjusted_items(doc, parsed_animals)
        if adjusted:
            doc.total_heads = adjusted["total_heads"]
            doc.set("items", [])
            for row in adjusted["items"]:
                doc.append("items", row)
        doc.save(ignore_permissions=True)
        doc.submit()
        doc = frappe.get_doc("Livestock Dispatch", dispatch_id)

    if (doc.confirmation_status or "") != "Completed":
        doc.confirm_dispatch(user, mode=mode or "None", animals=parsed_animals)
        doc = frappe.get_doc("Livestock Dispatch", dispatch_id)

    if doc.stock_entry:
        stock_entry = frappe.get_doc("Stock Entry", doc.stock_entry)
        if cint(stock_entry.docstatus or 0) == 0:
            stock_entry.submit()

    return _map_dispatch(frappe.get_doc("Livestock Dispatch", dispatch_id))


@frappe.whitelist()
def revert_dispatch(company_id, dispatch_id, user, reason=""):
    doc = _load_dispatch(company_id, dispatch_id)
    if not doc:
        return None
    doc.revert_dispatch(user, reason=reason or "")
    return _map_dispatch(frappe.get_doc("Livestock Dispatch", dispatch_id))


@frappe.whitelist()
def create_invoice_from_dispatch(company_id, dispatch_id):
    doc = _load_dispatch(company_id, dispatch_id)
    if not doc:
        return None
    if cint(doc.docstatus or 0) != 1:
        frappe.throw("El dispatch debe estar confirmado antes de generar la factura")

    if doc.sales_invoice:
        invoice = frappe.get_doc("Sales Invoice", doc.sales_invoice)
        return {
            "dispatch": _map_dispatch(doc),
            "invoiceId": doc.sales_invoice,
            "invoiceStatus": _map_invoice_status(invoice.as_dict()),
            "alreadyLinked": True,
        }

    if not doc.customer:
        frappe.throw("El dispatch no tiene cliente válido para facturar")
    if not doc.items:
        frappe.throw("El dispatch no tiene líneas válidas para facturar")

    doc.create_sales_invoice()
    refreshed = frappe.get_doc("Livestock Dispatch", dispatch_id)
    invoice = frappe.get_doc("Sales Invoice", refreshed.sales_invoice)
    return {
        "dispatch": _map_dispatch(refreshed),
        "invoiceId": refreshed.sales_invoice,
        "invoiceStatus": _map_invoice_status(invoice.as_dict()),
        "alreadyLinked": False,
    }


@frappe.whitelist()
def link_dispatch_invoice(company_id, dispatch_id, sales_invoice_id=None):
    doc = _load_dispatch(company_id, dispatch_id)
    if not doc:
        return None
    if cint(doc.docstatus or 0) == 2:
        frappe.throw("No se puede asociar factura a un dispatch cancelado")
    if sales_invoice_id:
        if not frappe.db.exists("Sales Invoice", sales_invoice_id):
            frappe.throw(f"Factura {sales_invoice_id} no encontrada")
    doc.db_set("sales_invoice", sales_invoice_id or None, update_modified=False)
    return _map_dispatch(frappe.get_doc("Livestock Dispatch", dispatch_id))


@frappe.whitelist()
def get_dispatch_reconciliation(company_id, dispatch_id):
    doc = _load_dispatch(company_id, dispatch_id)
    if not doc:
        return None

    dispatch = _map_dispatch(doc)
    dispatch_heads = cint(dispatch.get("totalHeads") or 0)
    linked_liquidation_id = dispatch.get("livestockSettlement") or None
    if not linked_liquidation_id:
        return {
            "dispatchId": dispatch_id,
            "linkedLiquidationId": None,
            "linkedLiquidationDoctype": None,
            "settlementId": None,
            "dispatchHeads": dispatch_heads,
            "settlementHeads": None,
            "difference": None,
            "status": "no_settlement",
        }

    linked_liquidation_doctype = None
    settlement_heads = None
    for doctype in COMMERCIAL_LIQUIDATION_DOCTYPES:
        rows = frappe.get_all(
            doctype,
            filters=[["name", "=", linked_liquidation_id]],
            fields=["name", "total_heads", "docstatus"],
            limit_page_length=1,
        )
        if rows:
            linked_liquidation_doctype = doctype
            settlement_heads = cint(rows[0].get("total_heads") or 0)
            break

    difference = dispatch_heads - settlement_heads if settlement_heads is not None else None
    status = "no_settlement" if difference is None else ("matched" if difference == 0 else "discrepancy")
    return {
        "dispatchId": dispatch_id,
        "linkedLiquidationId": linked_liquidation_id,
        "linkedLiquidationDoctype": linked_liquidation_doctype,
        "settlementId": linked_liquidation_id,
        "dispatchHeads": dispatch_heads,
        "settlementHeads": settlement_heads,
        "difference": difference,
        "status": status,
    }
