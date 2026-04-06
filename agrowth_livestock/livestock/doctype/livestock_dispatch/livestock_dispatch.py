import uuid
import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import flt
from agrowth_livestock.utils import (
    calculate_withholdings,
    add_nominal_withholdings_to_invoice,
    add_withholdings_to_invoice,
    get_company_default_account,
    get_iva_rate,
)

PLACEHOLDER_PREFIX = "SIN-SALIDA-"

READY_ANIMAL_STATUSES = {"Listo para salir"}
CATEGORY_ITEM_CODE_MAP = {
    "Ternero": "livestock-bovino-ternero",
    "Novillo": "livestock-bovino-novillo",
    "Vaquillona": "livestock-bovino-vaquillona",
    "Vaca": "livestock-bovino-vaca",
    "Toro": "livestock-bovino-toro",
}


def generate_placeholder_ear_tag():
    return f"{PLACEHOLDER_PREFIX}{uuid.uuid4().hex[:12].upper()}"


class LivestockDispatch(Document):
    # ── Validation ─────────────────────────────────────────────────────────────

    def validate(self):
        self.validate_mode()
        self.validate_items()
        self.calculate_totals()

    def validate_mode(self):
        if self.mode == "Full Batch":
            if not self.herd_batch:
                frappe.throw(_("Para modo 'Tropa Completa' debe seleccionar una tropa"))
        else:
            if self.herd_batch:
                frappe.throw(_("Para modo 'Mixto' no debe seleccionar tropa, agregue líneas manualmente"))
            if not self.items:
                frappe.throw(_("Para modo 'Mixto' debe agregar al menos una línea"))

    def validate_items(self):
        if not self.items:
            frappe.throw(_("El despacho debe tener al menos una línea"))

        for line in self.items:
            if not line.item_code:
                frappe.throw(_("Cada línea debe tener un artículo"))
            if line.qty_heads <= 0:
                frappe.throw(_("La cantidad de cabezas debe ser mayor a 0"))
            if not line.tax_rate:
                line.tax_rate = get_iva_rate(line.item_code)
            if not line.tax_amount:
                line.tax_amount = (line.amount or 0) * (line.tax_rate or 0) / 100
            self.validate_stock(line)

    def validate_stock(self, line):
        if not line.herd_batch:
            return
        batch = frappe.get_doc("Herd Batch", line.herd_batch)
        available_qty = 0
        for batch_line in batch.lines:
            if batch_line.item_code == line.item_code:
                available_qty = batch_line.qty_heads
                break
        if line.qty_heads > available_qty:
            frappe.throw(
                _("Stock insuficiente en tropa {0}: disponible {1}, solicitado {2}").format(
                    line.herd_batch, available_qty, line.qty_heads
                )
            )

    def calculate_totals(self):
        total_bruto = sum(line.amount or 0 for line in self.items)
        total_iva = sum(line.tax_amount or 0 for line in self.items)
        self.total_bruto = total_bruto
        self.total_iva = total_iva
        manual_iibb = flt(self.retencion_iibb_amount or 0)
        manual_iigg = flt(self.retencion_iigg_amount or 0)
        total_retenciones = manual_iibb + manual_iigg

        if total_retenciones <= 0 and self.customer and self.withholding_profile:
            withholdings = calculate_withholdings(self, total_bruto, "Customer")
            total_retenciones = sum(w["amount"] for w in withholdings)

        self.total_retenciones = total_retenciones
        ajuste_interno = flt(self.ajuste_interno or 0)
        self.total_neto = total_bruto + total_iva - self.total_retenciones + ajuste_interno

    # ── Submit (legacy direct-dispatch path, no LSL) ───────────────────────────

    def on_submit(self):
        if self.mode == "Full Batch":
            self.populate_from_batch()

        # Only create Sales Invoice if NOT linked to a commercial liquidation
        # (LSL-linked dispatches get their invoice from the commercial layer)
        if self.customer and not self.livestock_settlement:
            self.create_sales_invoice()

        self.create_stock_entry()
        self.update_herd_batch()

    def on_cancel(self):
        self.cancel_sales_invoice()
        self.cancel_stock_entry()
        self.restore_herd_batch()

    # ── confirm_dispatch (new physical-confirmation path) ─────────────────────

    @frappe.whitelist()
    def confirm_dispatch(self, user, mode="None"):
        """
        Confirm the physical exit of animals.
        Espejo de confirm_intake() — marca animales como Pending Exit,
        submitea el Stock Entry de Material Issue.

        Called from BFF via run_method after operator enters ear tags.
        The dispatch must be submitted (docstatus=1) at this point.
        """
        if self.confirmation_status == "Completed":
            frappe.throw(_("Este despacho ya fue confirmado"))

        self.confirmation_status = "Completed"
        self.confirmation_mode = mode
        self.confirmed_by = user
        self.confirmed_at = frappe.utils.now()

        if mode == "None":
            # Sin caravana: create placeholder ear tags and decrement stock generically
            self._seed_placeholder_animals()
        else:
            # Manual / File: validate each ear tag exists in ERP as active Animal
            self._validate_and_mark_animals()

        # Submit the Stock Entry created in on_submit
        self._submit_dispatch_stock_entry(user)

        # Update LSL reconciliation status if linked
        if self.livestock_settlement:
            self._update_lsl_reconciliation()

        self.save(ignore_permissions=True)
        return self

    @frappe.whitelist()
    def revert_dispatch(self, user, reason=""):
        """
        Revert a confirmed dispatch back to pending.
        Used when the physical exit was registered in error.
        """
        if self.confirmation_status != "Completed":
            frappe.throw(_("Solo se puede revertir un despacho confirmado"))

        if self.livestock_settlement:
            # Revert LSL reconciliation status
            lql = frappe.get_doc("Livestock Sales Liquidation", self.livestock_settlement)
            lql.db_set("reconciliation_status", "pending_dispatch", update_modified=False)

        # Mark animals back to Active
        for animal_row in self.animals or []:
            ear_tag = (animal_row.ear_tag_id or "").strip()
            if not ear_tag or ear_tag.startswith(PLACEHOLDER_PREFIX):
                continue
            try:
                animal_doc = frappe.get_doc("Animal", {"ear_tag_id": ear_tag})
                if self._get_animal_status(animal_doc) == "Pending Exit":
                    self._set_animal_status(animal_doc, "Active")
            except frappe.DoesNotExistError:
                continue

        self.confirmation_status = "Pending"
        self.confirmed_by = None
        self.confirmed_at = None
        if reason:
            self.revert_reason = reason

        self.save(ignore_permissions=True)
        return self

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _animal_status_is_supported(self):
        return bool(frappe.get_meta("Animal").get_field("status"))

    def _get_animal_status(self, animal_doc):
        if not self._animal_status_is_supported():
            return "Active"
        return getattr(animal_doc, "status", None) or "Active"

    def _set_animal_status(self, animal_doc, new_status):
        if not self._animal_status_is_supported():
            return
        animal_doc.status = new_status
        animal_doc.save(ignore_permissions=True)

    def _seed_placeholder_animals(self):
        """
        Sin caravana mode: seed placeholder ear tags for the expected quantity.
        One placeholder per head per dispatch line.
        """
        if self.animals:
            return  # already seeded

        for line in self.items:
            for _ in range(int(line.qty_heads or 0)):
                self.append("animals", {
                    "ear_tag_id": generate_placeholder_ear_tag(),
                    "category": line.category or "Otro",
                    "status": "Listo para salir",
                    "source_corral": self.warehouse or "",
                })

    def _validate_and_mark_animals(self):
        """
        Manual / File mode: look up each Animal by ear_tag_id.
        - If found and Active: mark as Pending Exit, record source corral
        - If not found: mark not_found_in_erp = 1 (does NOT block, logged only)
        """
        for animal_row in self.animals or []:
            ear_tag = (animal_row.ear_tag_id or "").strip()
            if not ear_tag:
                continue

            try:
                animal_doc = frappe.get_doc("Animal", {"ear_tag_id": ear_tag})
                current_status = self._get_animal_status(animal_doc)
                if self._animal_status_is_supported() and current_status not in ("Active", "Bajo observación"):
                    frappe.logger().warning(
                        f"[livestock_dispatch] Animal {ear_tag} is in state {current_status}, skipping"
                    )
                    continue

                if getattr(animal_doc, "disabled", 0):
                    frappe.logger().warning(
                        f"[livestock_dispatch] Animal {ear_tag} is disabled, skipping"
                    )
                    continue

                # Auto-fill source_corral from animal's current warehouse
                if not animal_row.source_corral and animal_doc.warehouse:
                    animal_row.source_corral = animal_doc.warehouse

                # Auto-fill category from animal
                if not animal_row.category and animal_doc.current_category:
                    animal_row.category = animal_doc.current_category

                # Mark animal as Pending Exit when lifecycle state exists
                self._set_animal_status(animal_doc, "Pending Exit")

                # Audit event
                frappe.get_doc({
                    "doctype": "Animal Event",
                    "animal": animal_doc.name,
                    "event_type": "Otro",
                    "event_date": frappe.utils.now(),
                    "notes": f"Marcado para salida física en despacho {self.name}",
                }).insert(ignore_permissions=True)

            except frappe.DoesNotExistError:
                animal_row.not_found_in_erp = 1
                frappe.logger().warning(
                    f"[livestock_dispatch] Animal {ear_tag} not found in ERP"
                )

    def _submit_dispatch_stock_entry(self, user):
        """
        Submit the Stock Entry (Material Issue) created in on_submit.
        Same pattern as intake: stock moves at physical confirmation, not at document submit.
        """
        if not self.stock_entry:
            frappe.logger().warning(
                f"[livestock_dispatch] No stock entry on dispatch {self.name}"
            )
            return

        se = frappe.get_doc("Stock Entry", self.stock_entry)
        if se.docstatus == 1:
            return  # already submitted

        se.submit()

    def _update_lsl_reconciliation(self):
        """
        After physical confirmation, update the linked LSL reconciliation status.
        Checks head count parity between LSL lines and dispatch animals.
        """
        try:
            lql = frappe.get_doc("Livestock Sales Liquidation", self.livestock_settlement)
            lql_heads = sum(line.qty_heads for line in lql.items)
            dispatch_heads = len([
                a for a in (self.animals or [])
                if (a.status or "") == "Listo para salir"
            ])

            new_status = "matched" if lql_heads == dispatch_heads else "discrepancy"
            lql.db_set("reconciliation_status", new_status, update_modified=False)
        except Exception as e:
            frappe.logger().warning(
                f"[livestock_dispatch] Could not update LSL reconciliation: {e}"
            )

    # ── Full Batch helpers (unchanged) ─────────────────────────────────────────

    def populate_from_batch(self):
        if not self.herd_batch:
            return
        self.items = []
        batch = frappe.get_doc("Herd Batch", self.herd_batch)
        for batch_line in batch.lines:
            tax_rate = self.get_iva_rate(batch_line.item_code)
            self.append("items", {
                "herd_batch": self.herd_batch,
                "item_code": batch_line.item_code,
                "category": batch_line.category,
                "qty_heads": batch_line.qty_heads,
                "avg_weight": getattr(batch_line, "avg_weight", 0),
                "unit_price": batch_line.unit_price,
                "amount": batch_line.amount,
                "tax_rate": tax_rate,
                "tax_amount": (batch_line.amount or 0) * (tax_rate / 100),
            })

    def get_iva_rate(self, item_code):
        from agrowth_livestock.utils import get_iva_rate_from_item
        return get_iva_rate_from_item(item_code)

    def create_sales_invoice(self):
        if self.sales_invoice:
            frappe.throw(_("Ya existe una Factura de Venta asociada"))
        si = frappe.new_doc("Sales Invoice")
        si.company = self.company
        si.customer = self.customer
        si.posting_date = self.posting_date
        for line in self.items:
            si_item = si.append("items")
            si_item.item_code = line.item_code
            si_item.qty = line.qty_heads
            si_item.rate = line.unit_price
            si_item.amount = line.amount
            si_item.warehouse = self.warehouse
        if self.total_iva > 0:
            vat_account = get_company_default_account(self.company, "default_vat_output_account")
            if vat_account:
                si.append("taxes", {
                    "charge_type": "On Net Total",
                    "account_head": vat_account,
                    "rate": 0,
                    "tax_amount": self.total_iva,
                    "description": "IVA",
                })
        manual_iibb = flt(self.retencion_iibb_amount or 0)
        manual_iigg = flt(self.retencion_iigg_amount or 0)

        if manual_iibb or manual_iigg:
            add_nominal_withholdings_to_invoice(
                si,
                company=self.company,
                iibb_amount=manual_iibb,
                iigg_amount=manual_iigg,
                is_purchase=False,
            )
        elif self.customer and self.withholding_profile and self.total_retenciones > 0:
            withholdings = calculate_withholdings(self, self.total_bruto, "Customer")
            add_withholdings_to_invoice(si, withholdings, is_purchase=False)
        si.insert(ignore_permissions=True)
        self.db_set("sales_invoice", si.name, update_modified=False)
        frappe.msgprint(_("Factura de Venta {0} creada en estado draft").format(si.name))

    def create_stock_entry(self):
        if self.stock_entry:
            frappe.throw(_("Ya existe una Salida de Stock asociada"))
        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Material Issue"
        se.purpose = "Material Issue"
        se.company = self.company
        se.posting_date = self.posting_date

        stock_lines = self._build_stock_entry_lines()
        for stock_line in stock_lines:
            se_item = se.append("items")
            se_item.item_code = stock_line["item_code"]
            se_item.qty = stock_line["qty"]
            se_item.s_warehouse = self.warehouse
            se_item.conversion_factor = 1
            se_item.allow_zero_valuation_rate = 1
        se.insert(ignore_permissions=True)
        self.db_set("stock_entry", se.name, update_modified=False)
        frappe.msgprint(_("Salida de Stock {0} creada en estado draft").format(se.name))

    def _build_stock_entry_lines(self):
        if any((line.herd_batch or "").strip() for line in self.items or []) or not self.animals:
            return [
                {
                    "item_code": line.item_code,
                    "qty": line.qty_heads,
                }
                for line in self.items
            ]

        category_counts: dict[str, int] = {}
        for animal_row in self.animals or []:
            ear_tag = (animal_row.ear_tag_id or "").strip()
            if not ear_tag or ear_tag.startswith(PLACEHOLDER_PREFIX):
                continue

            category = (animal_row.category or "").strip()
            if not category:
                try:
                    animal_doc = frappe.get_doc("Animal", {"ear_tag_id": ear_tag})
                    category = (getattr(animal_doc, "current_category", None) or "").strip()
                except frappe.DoesNotExistError:
                    category = ""

            if not category:
                frappe.logger().warning(
                    f"[livestock_dispatch] Could not infer category for animal {ear_tag} on dispatch {self.name}; falling back to dispatch lines"
                )
                return [
                    {
                        "item_code": line.item_code,
                        "qty": line.qty_heads,
                    }
                    for line in self.items
                ]

            category_counts[category] = category_counts.get(category, 0) + 1

        stock_lines = []
        for category, qty in category_counts.items():
            item_code = CATEGORY_ITEM_CODE_MAP.get(category)
            if not item_code:
                frappe.throw(_("No se encontró artículo ganadero para la categoría {0}").format(category))
            stock_lines.append({
                "item_code": item_code,
                "qty": qty,
            })

        return stock_lines

    def update_herd_batch(self):
        if self.mode == "Full Batch" and self.herd_batch:
            batch = frappe.get_doc("Herd Batch", self.herd_batch)
            batch.status = "Sold"
            batch.save(ignore_permissions=True)
        elif any((line.herd_batch or "").strip() for line in self.items or []):
            self.deduct_from_batches()
        elif self.animals:
            self.deduct_from_selected_animals()
        else:
            frappe.logger().info(
                f"[livestock_dispatch] Skipping herd batch deduction for {self.name}: no herd_batch on lines and no animals staged yet"
            )

    def deduct_from_batches(self):
        batch_quantities: dict = {}
        for line in self.items:
            if not line.herd_batch:
                continue
            if line.herd_batch not in batch_quantities:
                batch_quantities[line.herd_batch] = {}
            if line.item_code not in batch_quantities[line.herd_batch]:
                batch_quantities[line.herd_batch][line.item_code] = 0
            batch_quantities[line.herd_batch][line.item_code] += line.qty_heads
        for batch_name, items in batch_quantities.items():
            batch = frappe.get_doc("Herd Batch", batch_name)
            for batch_line in batch.lines:
                if batch_line.item_code in items:
                    batch_line.qty_heads -= items[batch_line.item_code]
                    if batch_line.qty_heads <= 0:
                        batch.status = "Sold"
            batch.save(ignore_permissions=True)

    def deduct_from_selected_animals(self):
        batch_quantities: dict[str, dict[str, int]] = {}

        for animal_row in self.animals or []:
            ear_tag = (animal_row.ear_tag_id or "").strip()
            if not ear_tag or ear_tag.startswith(PLACEHOLDER_PREFIX):
                continue

            try:
                animal_doc = frappe.get_doc("Animal", {"ear_tag_id": ear_tag})
            except frappe.DoesNotExistError:
                continue

            batch_name = (getattr(animal_doc, "current_herd_batch", None) or "").strip()
            category = (
                (animal_row.category or "").strip()
                or (getattr(animal_doc, "current_category", None) or "").strip()
            )

            if not batch_name or not category:
                frappe.logger().warning(
                    f"[livestock_dispatch] Could not infer herd batch/category for animal {ear_tag} on dispatch {self.name}"
                )
                continue

            batch_quantities.setdefault(batch_name, {})
            batch_quantities[batch_name][category] = batch_quantities[batch_name].get(category, 0) + 1

        for batch_name, categories in batch_quantities.items():
            batch = frappe.get_doc("Herd Batch", batch_name)

            for category, qty in categories.items():
                matched_line = next(
                    (line for line in batch.lines if (line.category or "").strip() == category),
                    None,
                )

                if not matched_line:
                    frappe.throw(
                        _("La tropa {0} no tiene línea para la categoría {1}").format(batch_name, category)
                    )

                available_qty = int(matched_line.qty_heads or 0)
                if qty > available_qty:
                    frappe.throw(
                        _("Stock insuficiente en tropa {0} para {1}: disponible {2}, solicitado {3}").format(
                            batch_name, category, available_qty, qty
                        )
                    )

                matched_line.qty_heads = available_qty - qty

            if batch.lines and all((line.qty_heads or 0) <= 0 for line in batch.lines):
                batch.status = "Sold"

            batch.save(ignore_permissions=True)

    def cancel_sales_invoice(self):
        if self.sales_invoice:
            si = frappe.get_doc("Sales Invoice", self.sales_invoice)
            if si.docstatus == 1:
                si.cancel()
            elif si.docstatus == 0:
                frappe.delete_doc("Sales Invoice", si.name)
            self.db_set("sales_invoice", None, update_modified=False)

    def cancel_stock_entry(self):
        if self.stock_entry:
            se = frappe.get_doc("Stock Entry", self.stock_entry)
            if se.docstatus == 1:
                se.cancel()
            elif se.docstatus == 0:
                frappe.delete_doc("Stock Entry", se.name)
            self.db_set("stock_entry", None, update_modified=False)

    def restore_herd_batch(self):
        if self.mode == "Full Batch" and self.herd_batch:
            batch = frappe.get_doc("Herd Batch", self.herd_batch)
            batch.status = "Active"
            batch.save(ignore_permissions=True)
        else:
            self.restore_batch_quantities()

    def restore_batch_quantities(self):
        batch_quantities: dict = {}
        for line in self.items:
            if line.herd_batch not in batch_quantities:
                batch_quantities[line.herd_batch] = {}
            if line.item_code not in batch_quantities[line.herd_batch]:
                batch_quantities[line.herd_batch][line.item_code] = 0
            batch_quantities[line.herd_batch][line.item_code] += line.qty_heads
        for batch_name, items in batch_quantities.items():
            batch = frappe.get_doc("Herd Batch", batch_name)
            for batch_line in batch.lines:
                if batch_line.item_code in items:
                    batch_line.qty_heads += items[batch_line.item_code]
            if batch.status == "Sold":
                batch.status = "Active"
            batch.save(ignore_permissions=True)
