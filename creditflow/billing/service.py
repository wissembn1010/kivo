from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import add_days, add_months, get_datetime, now_datetime

from creditflow.permissions import get_user_business
from creditflow.subscription import clear_request_cache, get_subscription

CURRENCY = "TND"


def tnd_to_millimes(amount):
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw(_("Invalid TND amount."))
    if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -3:
        frappe.throw(_("TND amount must be positive with at most three decimal places."))
    minor = value * Decimal("1000")
    if minor != minor.to_integral_value():
        frappe.throw(_("TND amount cannot be represented exactly in millimes."))
    return int(minor)


def _owner_business():
    if frappe.session.user == "Guest" or "OWNER" not in frappe.get_roles():
        frappe.throw(_("Only a Business OWNER can manage billing."), frappe.PermissionError)
    business = get_user_business()
    if not business:
        frappe.throw(_("Your user is not assigned to a CreditFlow Business."), frappe.PermissionError)
    return business


def _plan(plan_name):
    plan = frappe.get_doc("CreditFlow Plan", plan_name)
    if not plan.enabled:
        frappe.throw(_("The selected CreditFlow plan is not available."))
    return plan


def _amount(plan, billing_cycle):
    if billing_cycle == "MONTHLY":
        amount = plan.monthly_price
    elif billing_cycle == "ANNUAL":
        amount = plan.annual_price
    else:
        frappe.throw(_("Billing cycle must be MONTHLY or ANNUAL."))
    tnd_to_millimes(amount)
    return Decimal(str(amount)).quantize(Decimal("0.001"))


def is_paid_checkout_enabled():
    return frappe.conf.get("creditflow_paid_checkout_enabled") in (1, "1")


def create_checkout(plan, billing_cycle="MONTHLY", **unsupported):
    from creditflow.billing.bank import atomic
    return atomic(_create_checkout)(plan, billing_cycle, **unsupported)


def _create_checkout(plan, billing_cycle="MONTHLY", **unsupported):
    from creditflow.billing.bank import audit, bank_settings, public_payment
    if not is_paid_checkout_enabled():
        frappe.throw(_("Paid checkout is disabled on this site."), frappe.PermissionError)
    if unsupported:
        frappe.throw(_("Unsupported checkout fields."))
    business = _owner_business()
    frappe.db.get_value("Business", business, "name", for_update=True)
    clear_request_cache()
    selected = _plan(plan)
    amount = _amount(selected, billing_cycle)
    subscription = get_subscription(business)
    now = now_datetime()
    if subscription and subscription.plan != selected.name and subscription.current_period_end and get_datetime(subscription.current_period_end) > now:
        frappe.throw(_("Plan changes are available when the current paid period ends."))
    settings = bank_settings()
    payment = frappe.get_doc({"doctype": "CreditFlow Billing Payment", "business": business,
        "subscription": subscription.name if subscription else None, "plan": selected.name,
        "provider": "BANK_TRANSFER", "internal_order_id": "KIVO-" + frappe.generate_hash(length=24).upper(),
        "amount": amount, "currency": CURRENCY, "billing_cycle": billing_cycle,
        "status": "PENDING_PAYMENT", "created_at": now, "requested_by": frappe.session.user,
        "expires_at": add_days(now, settings["expiry_days"]), "destination_account": settings["rib"],
    }).insert(ignore_permissions=True)
    payment.checkout_url = "/creditflow-payment-return?payment=" + payment.name
    payment.save(ignore_permissions=True)
    audit(payment, "PENDING_PAYMENT", "Bank transfer request created using server plan price")
    return public_payment(payment) | {"checkout_url": payment.checkout_url}


def _activate(payment, paid_at):
    name = frappe.db.get_value("CreditFlow Subscription", {"business": payment.business}, "name")
    if payment.subscription and frappe.db.get_value("CreditFlow Subscription", payment.subscription, "business") != payment.business:
        frappe.throw(_("Billing subscription is outside your Business."), frappe.PermissionError)
    subscription = frappe.get_doc("CreditFlow Subscription", name, for_update=True) if name else None
    if not subscription:
        subscription = frappe.get_doc({"doctype": "CreditFlow Subscription", "business": payment.business,
            "plan": payment.plan, "status": "ACTIVE"})
    now = get_datetime(paid_at)
    current_end = get_datetime(subscription.current_period_end) if subscription.current_period_end else None
    if subscription.plan != payment.plan and current_end and current_end > now:
        frappe.throw(_("A paid period for another plan is still active."))
    period_start = current_end if current_end and current_end > now else now
    months = 12 if payment.billing_cycle == "ANNUAL" else 1
    period_end = add_months(period_start, months)
    subscription.plan = payment.plan
    subscription.status = "ACTIVE"
    subscription.subscription_start = subscription.subscription_start or now
    # An early renewal adds a future billing period while current access continues.
    subscription.current_period_start = (
        get_datetime(subscription.current_period_start)
        if current_end and current_end > now and subscription.current_period_start
        and get_datetime(subscription.current_period_start) <= now else now
    )
    subscription.current_period_end = period_end
    subscription.last_payment_at = now
    subscription.next_billing_date = period_end.date()
    subscription.external_provider = "BANK_TRANSFER"
    subscription.cancel_at_period_end = 0
    subscription.save(ignore_permissions=True)
    payment.subscription = subscription.name
    payment.billing_period_start = period_start
    payment.billing_period_end = period_end
    clear_request_cache()


def reconcile_provider_payment(provider_payment_id, expected_business=None):
    """Retired gateway entry point; never grants access."""
    return {"status": "UNKNOWN"}


def reconcile_owner_payment(payment_name):
    from creditflow.billing.bank import owner_payment, public_payment
    return public_payment(owner_payment(payment_name))


def request_cancellation():
    from creditflow.billing.bank import atomic
    return atomic(_request_cancellation)()


def _request_cancellation():
    business = _owner_business()
    frappe.db.get_value("Business", business, "name", for_update=True)
    name = frappe.db.get_value("CreditFlow Subscription", {"business": business}, "name")
    subscription = frappe.get_doc("CreditFlow Subscription", name, for_update=True) if name else None
    if not subscription or subscription.status != "ACTIVE":
        frappe.throw(_("There is no active subscription to cancel."))
    subscription.cancel_at_period_end = 1
    subscription.cancelled_at = now_datetime()
    subscription.save(ignore_permissions=True)
    clear_request_cache()
    return {"ok": True, "access_through": subscription.current_period_end}


def billing_history(business):
    if business != _owner_business():
        frappe.throw(_("Billing payment is outside your Business."), frappe.PermissionError)
    return frappe.get_all("CreditFlow Billing Payment", filters={"business": business},
        fields=["name", "internal_order_id", "plan", "amount", "currency", "billing_cycle", "status", "created_at", "paid_at", "checkout_url"],
        order_by="creation desc", limit=50)
