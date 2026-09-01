import frappe
from frappe.rate_limiter import rate_limit

from creditflow.billing.service import create_checkout, reconcile_owner_payment, reconcile_provider_payment, request_cancellation


@frappe.whitelist(methods=["POST"])
def start_checkout(plan, billing_cycle="MONTHLY", **unsupported):
    return create_checkout(plan, billing_cycle, **unsupported)


@frappe.whitelist(methods=["POST"])
def refresh_payment(payment):
    return reconcile_owner_payment(payment)


@frappe.whitelist(methods=["POST"])
def cancel_at_period_end():
    return request_cancellation()


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=120, seconds=60 * 60, methods="GET")
def konnect_webhook(payment_ref=None):
    return reconcile_provider_payment(payment_ref or "")
