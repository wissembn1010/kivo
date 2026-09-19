from hashlib import sha256

import frappe
from frappe.rate_limiter import rate_limit

from creditflow.billing.service import PAYMENT_REF_PATTERN, create_checkout, reconcile_owner_payment, reconcile_provider_payment, request_cancellation


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
    # Konnect documents GET notifications. They carry no payment authority and
    # expose no local payment identifiers or status, including for unknown refs.
    if not isinstance(payment_ref, str) or not PAYMENT_REF_PATTERN.fullmatch(payment_ref):
        return {"ok": True}
    # Atomic, site-scoped budget across source IPs. Do not cache acknowledgements:
    # a later notification may represent a newly completed payment.
    key = frappe.cache.make_key("konnect-webhook:" + sha256(payment_ref.encode()).hexdigest())
    count = frappe.cache.eval("""
        local count = redis.call('INCR', KEYS[1])
        if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
        return count
    """, 1, key, 60)
    if count > 10:
        frappe.throw("Too many payment notifications. Please retry later.", frappe.RateLimitExceededError)
    result = reconcile_provider_payment(payment_ref)
    if result.get("payment"):
        # Let Frappe commit the complete successful request, including verified
        # subscription changes. Exceptions still roll back the entire request.
        frappe.local.flags.commit = True
    return {"ok": True}
