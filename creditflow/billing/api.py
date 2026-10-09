import frappe
from frappe.rate_limiter import rate_limit
from creditflow.billing import bank
from creditflow.billing.service import create_checkout, reconcile_owner_payment, request_cancellation


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def start_checkout(plan, billing_cycle="MONTHLY", **unsupported):
    # frappe.call sends its routing command as a form field. It is not a
    # customer-controlled billing override; all other extra fields remain denied.
    if unsupported.get("cmd") == "creditflow.billing.api.start_checkout":
        unsupported.pop("cmd")
    return create_checkout(plan, billing_cycle, **unsupported)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=120, seconds=3600, methods=["POST"])
def refresh_payment(payment):
    return reconcile_owner_payment(payment)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def submit_proof(payment, filename, content):
    return bank.submit_proof(payment, filename, content)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=3600, methods=["POST"])
def admin_review(payment, action, transaction_id=None, amount=None, transaction_date=None,
        reference=None, destination_account=None, notes=None):
    return bank.admin_review(payment, action, transaction_id, amount, transaction_date,
        reference, destination_account, notes)


@frappe.whitelist()
def admin_payment_details(payment):
    return bank.admin_payment_details(payment)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def cancel_at_period_end():
    return request_cancellation()


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=120, seconds=3600, methods=["GET"])
def konnect_webhook(payment_ref=None):
    """Retired callback; historical retries cannot grant entitlements."""
    return {"ok": True}
