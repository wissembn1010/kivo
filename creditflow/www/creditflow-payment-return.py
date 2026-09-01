import frappe
from creditflow.billing.service import _owner_business


def get_context(context):
    context.no_cache=1; context.title="Checking payment"
    context.payment=frappe.form_dict.get("payment") or ""
    business=_owner_business()
    if context.payment and frappe.db.get_value("CreditFlow Billing Payment",context.payment,"business")!=business:
        frappe.throw("Payment not found",frappe.PermissionError)
