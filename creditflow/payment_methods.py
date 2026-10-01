"""Canonical payment vocabulary; workflow support remains explicit."""
import frappe
from frappe import _

CUSTOMER_PAYMENT_METHODS = frozenset({"CASH", "BANK_TRANSFER", "CHEQUE", "CARD", "OTHER"})
SUPPLIER_PAYMENT_METHODS = frozenset({"CASH", "BANK_TRANSFER", "CHEQUE", "TRAITE", "OTHER"})


def normalize_payment_method(value, allowed, *, default=None):
    method = (value or default or "").strip().upper()
    if method == "BANK TRANSFER":
        method = "BANK_TRANSFER"
    if method and method not in allowed:
        frappe.throw(_("Payment Method is not supported."))
    return method or None


def migrate_payment_methods():
    # Only known aliases/case/whitespace; unknown historical values are preserved.
    for doctype, allowed in (
        ("Sale", CUSTOMER_PAYMENT_METHODS), ("Payment", CUSTOMER_PAYMENT_METHODS),
        ("Purchase", SUPPLIER_PAYMENT_METHODS), ("Supplier Payment", SUPPLIER_PAYMENT_METHODS),
    ):
        for method in sorted(allowed):
            aliases = (method, "BANK TRANSFER") if method == "BANK_TRANSFER" else (method,)
            for alias in aliases:
                frappe.db.sql(
                    f"UPDATE `tab{doctype}` SET payment_method = %s "
                    "WHERE UPPER(TRIM(payment_method)) = %s AND BINARY payment_method != BINARY %s",
                    (method, alias, method),
                )
