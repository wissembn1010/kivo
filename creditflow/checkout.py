"""Read-only, authorized customer context for the Sale form."""
from decimal import Decimal

import frappe
from frappe import _


@frappe.whitelist()
def get_customer_context(business, customer):
    doc = frappe.get_doc("Customer", customer)
    doc.check_permission("read")
    if doc.business != business:
        frappe.throw(_("Customer must belong to the same Business as the Sale."), frappe.PermissionError)
    balance = Decimal(doc.get_ledger_balance())
    limit = Decimal(str(doc.credit_limit or 0)).quantize(Decimal("0.001"))
    # Zero means no credit limit in Sale.validate_credit_limit, not zero credit.
    available = limit - balance if limit > 0 else None
    return {
        "business": doc.business,
        "customer": doc.name,
        "price_type": doc.price_type or "RETAIL",
        "credit_limit": str(limit),
        "current_debt": str(balance),
        "available_credit": str(available) if available is not None else None,
    }
