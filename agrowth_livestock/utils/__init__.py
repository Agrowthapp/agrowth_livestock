# Agrowth Livestock Utilities
import frappe
from frappe import _
from frappe.utils import flt


PURCHASE_WITHHOLDING_ACCOUNT_NAMES = {
    "IIBB": "Impuesto a los Ingresos Brutos a Pagar",
    "IIGG": "Impuesto a las Ganancias a Pagar",
    "Sellos": "Retención Sellos - AC",
    "Comisión": "Comisión Gasto - AC",
}

SALES_WITHHOLDING_ACCOUNT_NAMES = {
    "IIBB": "Anticipos Impuesto a los Ingresos Brutos",
    "IIGG": "Percepciones y Retenciones Impto. a las Ganancias",
    "Sellos": "Retención Sellos - AC",
    "Comisión": "Comisión Gasto - AC",
}

FALLBACK_COMPANY_ACCOUNT_NAMES = {
    "default_vat_input_account": ["IVA Crédito Fiscal", "VAT"],
    "default_vat_output_account": ["IVA Débito Fiscal", "VAT"],
}


def get_iva_rate_from_item(item_code):
    """Get VAT rate from Item Tax Template"""
    try:
        item = frappe.get_doc("Item", item_code)

        if item.tax_category:
            tax_category = frappe.get_doc("Item Tax Category", item.tax_category)
            for row in tax_category.taxes:
                if "IVA" in row.tax_type:
                    return row.tax_rate
    except Exception:
        pass

    # Default VAT 21%
    return 21.0


def get_iva_rate(item_code):
    """Alias for get_iva_rate_from_item"""
    return get_iva_rate_from_item(item_code)


def calculate_withholdings(doc, base_amount, counterparty_type="Supplier"):
    """
    Calculate withholdings based on withholding profile.
    """
    withholdings = []

    tax_profile_field = "tax_profile" if hasattr(doc, "tax_profile") else "withholding_profile"
    profile_name = getattr(doc, tax_profile_field, None)

    if not profile_name:
        return withholdings

    try:
        profile = frappe.get_doc("Withholding Profile", profile_name)
    except frappe.DoesNotExistError:
        frappe.msgprint(_("Withholding Profile {0} not found").format(profile_name))
        return withholdings

    doc_company = getattr(doc, "company", None)
    if doc_company and profile.company and profile.company != doc_company:
        frappe.msgprint(_("Withholding Profile {0} does not belong to this company").format(profile_name))
        return withholdings

    if not profile.is_active:
        return withholdings

    if profile.province and hasattr(doc, "province") and profile.province != doc.province:
        return withholdings

    if profile.counterparty_type not in [counterparty_type, "Both"]:
        return withholdings

    rules = profile.rules or []
    today = frappe.utils.today()
    is_purchase = counterparty_type == "Supplier"

    for rule in rules:
        if rule.effective_from and rule.effective_from > today:
            continue
        if rule.effective_to and rule.effective_to < today:
            continue
        if rule.min_base and base_amount < rule.min_base:
            continue

        amount = 0
        if rule.rate and rule.rate > 0:
            amount = base_amount * (rule.rate / 100)
        elif rule.fixed_amount:
            amount = rule.fixed_amount

        if amount <= 0:
            continue

        account = get_withholding_account(rule.withholding_type, doc.company, is_purchase=is_purchase)

        withholdings.append({
            "type": rule.withholding_type,
            "account": account,
            "rate": rule.rate or 0,
            "amount": amount,
            "description": get_withholding_description(rule.withholding_type, rule.tax_category),
        })

    return withholdings


def get_withholding_account(withholding_type, company, is_purchase=True):
    """Return the configured chart account for a withholding type."""
    account_map = PURCHASE_WITHHOLDING_ACCOUNT_NAMES if is_purchase else SALES_WITHHOLDING_ACCOUNT_NAMES
    account_name = account_map.get(withholding_type)
    if not account_name:
        return None
    return frappe.db.get_value(
        "Account",
        {"account_name": account_name, "company": company, "is_group": 0},
        "name",
    )


def get_withholding_description(withholding_type, tax_category=None):
    descriptions = {
        "IIBB": "Retención IIBB",
        "IIGG": "Retención IIGG",
        "Sellos": "Retención Sellos",
        "Comisión": "Comisión",
    }

    desc = descriptions.get(withholding_type, withholding_type)
    if tax_category:
        desc += f" ({tax_category})"
    return desc


def _append_withholding_tax_row(invoice_doc, account, amount, description, is_purchase=True, rate=0):
    amount = flt(amount)
    if not account or amount <= 0:
        return

    row = {
        "charge_type": "Actual",
        "account_head": account,
        "rate": rate or 0,
        "description": description,
    }

    if is_purchase:
        row["add_deduct_tax"] = "Deduct"
        row["tax_amount"] = amount
    else:
        row["tax_amount"] = -amount

    invoice_doc.append("taxes", row)


def add_withholdings_to_invoice(invoice_doc, withholdings, is_purchase=True):
    """Add withholding lines to a Purchase or Sales Invoice."""
    if not withholdings:
        return

    for w in withholdings:
        _append_withholding_tax_row(
            invoice_doc,
            w.get("account"),
            w.get("amount"),
            w.get("description", w.get("type", "Retención")),
            is_purchase=is_purchase,
            rate=w.get("rate", 0),
        )


def add_nominal_withholdings_to_invoice(invoice_doc, company, iibb_amount=0, iigg_amount=0, is_purchase=True):
    """Append nominal IIBB/IIGG withholding rows directly to an invoice."""
    mappings = [
        ("IIBB", flt(iibb_amount), "Retención IIBB"),
        ("IIGG", flt(iigg_amount), "Retención IIGG"),
    ]

    for withholding_type, amount, description in mappings:
        if amount <= 0:
            continue
        account = get_withholding_account(withholding_type, company, is_purchase=is_purchase)
        _append_withholding_tax_row(invoice_doc, account, amount, description, is_purchase=is_purchase)


def validate_stock_availability(item_code, warehouse, qty):
    """Validate that there's enough stock available"""
    from erpnext.stock.utils import get_stock_balance

    current_stock = get_stock_balance(item_code, warehouse)
    if current_stock < qty:
        frappe.throw(
            _("Stock insufficient for {0}: available {1}, requested {2}").format(
                item_code, current_stock, qty
            )
        )


def get_company_default_account(company, account_type):
    """Get company default account by type with chart fallbacks."""
    company_doc = frappe.get_doc("Company", company)

    account_field_map = {
        "default_vat_input_account": "vat_input_account",
        "default_vat_output_account": "vat_output_account",
    }

    field = account_field_map.get(account_type)
    if field:
        configured = getattr(company_doc, field, None)
        if configured:
            return configured

    for account_name in FALLBACK_COMPANY_ACCOUNT_NAMES.get(account_type, []):
        account = frappe.db.get_value(
            "Account",
            {"company": company, "account_name": account_name, "is_group": 0},
            "name",
        )
        if account:
            return account

    return None


def track_modification(doc, method=None):
    """Hook for tracking modifications"""
    pass


def track_cancellation(doc, method=None):
    """Hook for tracking cancellations"""
    pass


def track_submission(doc, method=None):
    """Hook for tracking submissions"""
    pass
