# Agrowth Livestock Utilities
import frappe
from frappe import _


def get_iva_rate_from_item(item_code):
    """Get VAT rate from Item Tax Template"""
    try:
        item = frappe.get_doc("Item", item_code)
        
        if item.tax_category:
            tax_category = frappe.get_doc("Item Tax Category", item.tax_category)
            for row in tax_category.taxes:
                if "IVA" in row.tax_type:
                    return row.tax_rate
    except:
        pass
    
    # Default VAT 21%
    return 21.0


def get_iva_rate(item_code):
    """Alias for get_iva_rate_from_item"""
    return get_iva_rate_from_item(item_code)


def calculate_withholdings(doc, base_amount, counterparty_type="Supplier"):
    """
    Calculate withholdings based on withholding profile.
    
    Args:
        doc: The document (LivestockSettlement or LivestockDispatch)
        base_amount: The base amount for calculation
        counterparty_type: "Supplier" or "Customer"
    
    Returns:
        List of withholding dictionaries with type, account, rate, amount
    """
    withholdings = []
    
    # Get withholding profile
    tax_profile_field = "tax_profile" if hasattr(doc, "tax_profile") else "withholding_profile"
    profile_name = getattr(doc, tax_profile_field, None)
    
    if not profile_name:
        return withholdings
    
    try:
        profile = frappe.get_doc("Withholding Profile", profile_name)
    except frappe.DoesNotExistError:
        frappe.msgprint(_("Withholding Profile {0} not found").format(profile_name))
        return withholdings
    
    # Check if profile is active
    if not profile.is_active:
        return withholdings
    
    # Check province match
    if profile.province and hasattr(doc, "province"):
        if profile.province != doc.province:
            return withholdings
    
    # Check counterparty type
    if profile.counterparty_type not in [counterparty_type, "Both"]:
        return withholdings
    
    # Get rules
    rules = profile.rules or []
    today = frappe.utils.today()
    
    for rule in rules:
        # Check validity period
        if rule.effective_from and rule.effective_from > today:
            continue
        if rule.effective_to and rule.effective_to < today:
            continue
        
        # Check minimum base
        if rule.min_base and base_amount < rule.min_base:
            continue
        
        # Calculate amount
        amount = 0
        if rule.rate and rule.rate > 0:
            amount = base_amount * (rule.rate / 100)
        elif rule.fixed_amount:
            amount = rule.fixed_amount
        
        if amount <= 0:
            continue
        
        # Get withholding account
        account = get_withholding_account(rule.withholding_type, doc.company)
        
        withholdings.append({
            "type": rule.withholding_type,
            "account": account,
            "rate": rule.rate or 0,
            "amount": amount,
            "description": get_withholding_description(rule.withholding_type, rule.tax_category)
        })
    
    return withholdings


def get_withholding_account(withholding_type, company):
    """
    Get the default withholding account for a given type.
    These should be configured in the Company or Chart of Accounts.
    """
    account_map = {
        "IIBB": "Retención IIBB - AC",
        "IIGG": "Retención Ganancias - AC",
        "Sellos": "Retención Sellos - AC",
        "Comisión": "Comisión Gasto - AC"
    }
    
    default_account = account_map.get(withholding_type)
    
    if default_account:
        # Try to find the account
        account = frappe.db.get_value(
            "Account",
            {"account_name": default_account, "company": company, "is_group": 0},
            "name"
        )
        if account:
            return account
    
    # Fallback: return None and let the invoice handle it
    return None


def get_withholding_description(withholding_type, tax_category=None):
    """Get description for withholding"""
    descriptions = {
        "IIBB": "Retención Ing. Brutos",
        "IIGG": "Retención Ganancias",
        "Sellos": "Retención Sellos",
        "Comisión": "Comisión"
    }
    
    desc = descriptions.get(withholding_type, withholding_type)
    
    if tax_category:
        desc += f" ({tax_category})"
    
    return desc


def add_withholdings_to_invoice(invoice_doc, withholdings, is_purchase=True):
    """
    Add withholding lines to a Purchase or Sales Invoice.
    
    Args:
        invoice_doc: The invoice document
        withholdings: List of withholding dictionaries
        is_purchase: True for Purchase Invoice, False for Sales Invoice
    """
    if not withholdings:
        return
    
    for w in withholdings:
        if not w.get("account"):
            continue
        
        # Add as tax/deduction
        invoice_doc.append("taxes", {
            "charge_type": "Actual",
            "account_head": w["account"],
            "rate": w.get("rate", 0),
            "tax_amount": w["amount"],
            "description": w.get("description", w.get("type", "Retención"))
        })


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
    """Get company default account by type"""
    company_doc = frappe.get_doc("Company", company)
    
    account_field_map = {
        "default_vat_input_account": "vat_input_account",
        "default_vat_output_account": "vat_output_account"
    }
    
    field = account_field_map.get(account_type)
    if field:
        return getattr(company_doc, field, None)
    
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
