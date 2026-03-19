import frappe
from frappe.model.document import Document
from frappe import _
from agrowth_livestock.utils import (
    calculate_withholdings,
    add_withholdings_to_invoice,
    get_company_default_account,
)


class LivestockSalesLiquidation(Document):
    # ── Validation ─────────────────────────────────────────────────────────────

    def validate(self):
        self.validate_items()
        self.calculate_totals()

    def validate_items(self):
        if not self.items:
            frappe.throw(_("La liquidación debe tener al menos una línea"))

        for line in self.items:
            if not line.item_code:
                frappe.throw(_("Cada línea debe tener un artículo"))
            if line.qty_heads <= 0:
                frappe.throw(_("La cantidad de cabezas debe ser mayor a 0"))
            if line.price_mode == "Por Kg" and not line.avg_weight:
                frappe.throw(_("Si el precio es por kg, debe especificar el peso promedio"))
            if not line.tax_rate:
                line.tax_rate = 10.5  # IVA ganadero estándar Argentina
            if not line.tax_amount:
                line.tax_amount = (line.amount or 0) * (line.tax_rate or 0) / 100

    def calculate_totals(self):
        total_bruto = sum(line.amount or 0 for line in self.items)
        total_iva = sum(line.tax_amount or 0 for line in self.items)

        self.total_bruto = total_bruto
        self.total_iva = total_iva

        withholdings = calculate_withholdings(self, total_bruto, "Customer")
        self.total_retenciones = sum(w["amount"] for w in withholdings)

        # Neto = Bruto + IVA - Retenciones - Comisiones
        self.total_neto = (
            total_bruto
            + total_iva
            - self.total_retenciones
            - (self.total_comisiones or 0)
        )

    # ── Submit ─────────────────────────────────────────────────────────────────

    def on_submit(self):
        """
        Espejo de LivestockSettlement.on_submit() para el lado ventas.
        Al confirmar la liquidación comercial:
        1. Crea un Livestock Dispatch en estado DRAFT (pendiente de salida física)
        2. Registra el despacho pendiente para conciliación
        El dispatch se submitea cuando la salida física es confirmada operacionalmente.
        """
        self._create_pending_dispatch()

    def _create_pending_dispatch(self):
        """
        Crea un Livestock Dispatch draft vinculado a esta liquidación.
        El dispatch queda pendiente hasta que operaciones confirma la salida física.
        Patrón espejo: igual que LivestockSettlement crea un LivestockIntake pendiente.
        """
        if self.linked_dispatch:
            frappe.throw(_("Ya existe un Despacho vinculado a esta liquidación"))

        if not frappe.db.exists("DocType", "Livestock Dispatch"):
            frappe.log_error(
                "Livestock Dispatch doctype not found. Cannot create pending dispatch.",
                "LivestockSalesLiquidation._create_pending_dispatch",
            )
            return

        dispatch = frappe.new_doc("Livestock Dispatch")
        dispatch.company = self.company
        dispatch.customer = self.customer
        dispatch.posting_date = self.posting_date
        dispatch.livestock_settlement = self.name   # vínculo back al doctype comercial
        dispatch.mode = "Mixed"                      # sin tropa aún — se asigna al confirmar
        dispatch.warehouse = self.warehouse or ""
        dispatch.province = self.province or "Buenos Aires"
        dispatch.withholding_profile = self.withholding_profile or ""

        # Trasladar líneas comerciales → líneas del dispatch
        for line in self.items:
            dispatch_line = dispatch.append("items")
            dispatch_line.item_code = line.item_code
            dispatch_line.category = line.category or "Otro"
            dispatch_line.qty_heads = line.qty_heads
            dispatch_line.avg_weight = line.avg_weight or 0
            dispatch_line.unit_price = line.unit_price
            dispatch_line.amount = line.amount or 0
            dispatch_line.tax_rate = line.tax_rate or 10.5
            dispatch_line.tax_amount = line.tax_amount or 0

        dispatch.total_bruto = self.total_bruto
        dispatch.total_iva = self.total_iva
        dispatch.total_retenciones = self.total_retenciones
        dispatch.total_neto = self.total_neto

        dispatch.insert(ignore_permissions=True)

        self.db_set("linked_dispatch", dispatch.name, update_modified=False)
        self.db_set("reconciliation_status", "pending_dispatch", update_modified=False)

        frappe.msgprint(
            _("Despacho pendiente {0} creado. Confirmalo desde /ganaderia/salidas cuando la hacienda salga físicamente.").format(
                dispatch.name
            )
        )

    # ── Cancel ─────────────────────────────────────────────────────────────────

    def on_cancel(self):
        """
        Reversa la cancelación: si el dispatch aún está en draft, lo elimina.
        Si ya fue submitido (salida confirmada), bloquea la cancelación.
        """
        self._cancel_or_remove_dispatch()

    def _cancel_or_remove_dispatch(self):
        if not self.linked_dispatch:
            return

        if not frappe.db.exists("Livestock Dispatch", self.linked_dispatch):
            self.db_set("linked_dispatch", None, update_modified=False)
            self.db_set("reconciliation_status", "pending_settlement", update_modified=False)
            return

        dispatch = frappe.get_doc("Livestock Dispatch", self.linked_dispatch)

        if dispatch.docstatus == 1:
            frappe.throw(
                _(
                    "No se puede cancelar la liquidación: el despacho {0} ya fue confirmado. "
                    "Cancelá primero el despacho."
                ).format(self.linked_dispatch)
            )

        if dispatch.docstatus == 0:
            frappe.delete_doc("Livestock Dispatch", dispatch.name, ignore_permissions=True)

        self.db_set("linked_dispatch", None, update_modified=False)
        self.db_set("reconciliation_status", "pending_settlement", update_modified=False)

    # ── Whitelisted methods ────────────────────────────────────────────────────

    @frappe.whitelist()
    def reconcile_with_dispatch(self, dispatch_id: str) -> dict:
        """
        Vincula manualmente esta liquidación a un dispatch existente
        y verifica si las cabezas coinciden (matched vs discrepancy).
        Llamado desde el BFF cuando el operador confirma el matching.
        """
        if self.docstatus != 1:
            frappe.throw(_("Solo se puede reconciliar una liquidación confirmada"))

        if not frappe.db.exists("Livestock Dispatch", dispatch_id):
            frappe.throw(_("El despacho {0} no existe").format(dispatch_id))

        dispatch = frappe.get_doc("Livestock Dispatch", dispatch_id)

        # Verificar que el dispatch pertenezca al mismo cliente y empresa
        if dispatch.customer != self.customer:
            frappe.throw(
                _("El despacho {0} corresponde a otro cliente").format(dispatch_id)
            )

        # Calcular cabezas declaradas en liquidación vs despacho
        lql_heads = sum(line.qty_heads for line in self.items)
        dispatch_heads = sum(
            (line.qty_heads for line in dispatch.items), 0
        ) if dispatch.items else getattr(dispatch, "total_heads", 0)

        if lql_heads == dispatch_heads:
            new_status = "matched"
        else:
            new_status = "discrepancy"

        self.db_set("linked_dispatch", dispatch_id, update_modified=False)
        self.db_set("reconciliation_status", new_status, update_modified=False)

        # Vincular el dispatch de vuelta a la liquidación
        if hasattr(dispatch, "livestock_settlement"):
            dispatch.db_set("livestock_settlement", self.name, update_modified=False)

        return {
            "status": new_status,
            "lql_heads": lql_heads,
            "dispatch_heads": dispatch_heads,
            "dispatch_id": dispatch_id,
        }
