import frappe
from frappe import _
from frappe.utils import now, getdate


def handle_purchase_invoice_submit(doc, method=None):
    """
    Hook ejecutado al提交 Purchase Invoice.
    Si document_type = 'Liquidación Hacienda', crea Livestock Settlement y Herd Batch.
    """
    if doc.get("document_type") != "Liquidación Hacienda":
        return

    if doc.get("livestock_settlement"):
        frappe.throw(_("Esta factura ya tiene una Liquidación Ganadera asociada."))

    if not doc.supplier:
        frappe.throw(_("La factura debe tener un proveedor."))

    settlement = frappe.get_doc({
        "doctype": "Livestock Settlement",
        "company": doc.company,
        "supplier": doc.supplier,
        "posting_date": getdate(),
        "document_number": doc.name,
        "warehouse": doc.set_warehouse or "",
        "province": "Buenos Aires",
        "requires_individualization": 0
    })

    item_groups = {}
    for item in doc.items:
        item_doc = frappe.get_doc("Item", item.item_code)
        if not item_doc.get("is_livestock_category"):
            continue

        if item.item_code not in item_groups:
            item_groups[item.item_code] = {
                "item_code": item.item_code,
                "qty": 0,
                "rate": item.rate,
                "amount": 0
            }

        item_groups[item.item_code]["qty"] += item.qty
        item_groups[item.item_code]["amount"] += item.amount

    for item_code, data in item_groups.items():
        settlement.append("items", {
            "item_code": data["item_code"],
            "qty": data["qty"],
            "rate": data["rate"],
            "amount": data["amount"]
        })

    settlement.insert(ignore_permissions=True)
    settlement.submit()

    doc.db_set("livestock_settlement", settlement.name)
    doc.save(ignore_permissions=True)

    frappe.msgprint(_("Liquidación Ganadera {0} creada exitosamente.").format(settlement.name))


def handle_purchase_invoice_cancel(doc, method=None):
    """
    Hook ejecutado al cancelar Purchase Invoice.
    Cancela el Livestock Settlement asociado si existe.
    """
    if doc.get("document_type") != "Liquidación Hacienda":
        return

    settlement_name = doc.get("livestock_settlement")
    if not settlement_name:
        return

    if not frappe.db.exists("Livestock Settlement", settlement_name):
        return

    settlement = frappe.get_doc("Livestock Settlement", settlement_name)

    if settlement.docstatus == 1:
        if frappe.db.exists("Herd Batch", {"livestock_settlement": settlement.name}):
            frappe.throw(_("No se puede cancelar la factura. Existen Eventos posteriores sobre las tropas creadas."))

        settlement.cancel()

    doc.db_set("livestock_settlement", None)
    frappe.msgprint(_("Liquidación Ganadera {0} cancelada.").format(settlement_name))


def handle_sales_invoice_submit(doc, method=None):
    """
    Hook ejecutado al提交 Sales Invoice.
    Si document_type = 'Liquidación Hacienda', crea Livestock Dispatch.
    """
    if doc.get("document_type") != "Liquidación Hacienda":
        return

    if doc.get("livestock_dispatch"):
        frappe.throw(_("Esta factura ya tiene un Despacho Ganadero asociado."))

    if not doc.customer:
        frappe.throw(_("La factura debe tener un cliente."))

    dispatch = frappe.get_doc({
        "doctype": "Livestock Dispatch",
        "company": doc.company,
        "customer": doc.customer,
        "posting_date": getdate(),
        "mode": "Full Batch",
        "warehouse": doc.set_warehouse or "",
        "province": "Buenos Aires"
    })

    for item in doc.items:
        dispatch.append("items", {
            "item_code": item.item_code,
            "qty": item.qty,
            "rate": item.rate,
            "amount": item.amount
        })

    dispatch.insert(ignore_permissions=True)
    dispatch.submit()

    doc.db_set("livestock_dispatch", dispatch.name)
    doc.save(ignore_permissions=True)

    frappe.msgprint(_("Despacho Ganadero {0} creado exitosamente.").format(dispatch.name))


def handle_sales_invoice_cancel(doc, method=None):
    """
    Hook ejecutado al cancelar Sales Invoice.
    Cancela el Livestock Dispatch asociado si existe.
    """
    if doc.get("document_type") != "Liquidación Hacienda":
        return

    dispatch_name = doc.get("livestock_dispatch")
    if not dispatch_name:
        return

    if not frappe.db.exists("Livestock Dispatch", dispatch_name):
        return

    dispatch = frappe.get_doc("Livestock Dispatch", dispatch_name)

    if dispatch.docstatus == 1:
        dispatch.cancel()

    doc.db_set("livestock_dispatch", None)
    frappe.msgprint(_("Despacho Ganadero {0} cancelado.").format(dispatch_name))
