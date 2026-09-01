import json
import re
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import add_months, get_datetime, now_datetime

from creditflow.billing.providers.konnect import KonnectProvider
from creditflow.permissions import get_user_business, has_cross_business_access
from creditflow.subscription import clear_request_cache, get_subscription

CURRENCY = "TND"
PAYMENT_REF_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
PROVIDERS = {"KONNECT": KonnectProvider}


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


def _provider(name="KONNECT"):
    provider = PROVIDERS.get(name)
    if not provider:
        frappe.throw(_("Unsupported billing provider."))
    return provider()


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


def create_checkout(plan, billing_cycle="MONTHLY", **unsupported):
    if unsupported:
        frappe.throw(_("Unsupported checkout fields."))
    business = _owner_business()
    selected = _plan(plan)
    amount = _amount(selected, billing_cycle)
    subscription = get_subscription(business)
    now = now_datetime()
    if subscription and subscription.plan != selected.name and subscription.current_period_end and get_datetime(subscription.current_period_end) > now:
        frappe.throw(_("Plan changes are available when the current paid period ends."))
    order_id = f"CF-{frappe.generate_hash(length=24)}"
    payment = frappe.get_doc({
        "doctype":"CreditFlow Billing Payment", "business":business,
        "subscription":subscription.name if subscription else None, "plan":selected.name,
        "provider":"KONNECT", "internal_order_id":order_id, "amount":amount,
        "currency":CURRENCY, "billing_cycle":billing_cycle, "status":"PENDING", "created_at":now,
    }).insert(ignore_permissions=True)
    payment.amount_minor = tnd_to_millimes(amount)
    user = frappe.get_doc("User", frappe.session.user)
    business_doc = frappe.get_doc("Business", business)
    try:
        result = _provider().create_checkout(payment, {
            "first_name":user.first_name, "last_name":user.last_name, "email":user.email,
            "phone":business_doc.phone,
        })
        if frappe.db.exists("CreditFlow Billing Payment", {"provider_payment_id": result["provider_payment_id"]}):
            payment.last_reconciliation_message = "Provider returned an already-used payment reference"
            payment.save(ignore_permissions=True)
            return {"payment":payment.name, "status":"PENDING", "checkout_url":None,
                    "message":_("The payment provider returned an invalid reference. Please retry safely.")}
        payment.provider_payment_id = result["provider_payment_id"]
        payment.checkout_url = result["checkout_url"]
        payment.last_reconciliation_message = "Checkout created"
        payment.save(ignore_permissions=True)
        return {"payment":payment.name, "status":payment.status, "checkout_url":payment.checkout_url}
    except Exception:
        frappe.clear_messages()
        payment.last_reconciliation_message = "Provider temporarily unavailable during checkout creation"
        payment.save(ignore_permissions=True)
        return {"payment":payment.name, "status":"PENDING", "checkout_url":None,
                "message":_("The payment provider is temporarily unavailable. Please retry safely.")}


def _lock_payment(provider_payment_id):
    rows = frappe.db.sql("SELECT name FROM `tabCreditFlow Billing Payment` WHERE provider_payment_id=%s FOR UPDATE", provider_payment_id)
    return frappe.get_doc("CreditFlow Billing Payment", rows[0][0]) if rows else None


def _safe_metadata(details):
    return json.dumps({key:details.get(key) for key in ("provider_status","amount_minor","currency","order_id","receiver_wallet_id","payment_id")}, default=str)


def _activate(payment, paid_at):
    subscription = frappe.get_doc("CreditFlow Subscription", payment.subscription) if payment.subscription else None
    if not subscription:
        subscription = frappe.get_doc({"doctype":"CreditFlow Subscription","business":payment.business,"plan":payment.plan,"status":"ACTIVE"}).insert(ignore_permissions=True)
        payment.subscription = subscription.name
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
    subscription.current_period_start = period_start
    subscription.current_period_end = period_end
    subscription.last_payment_at = now
    subscription.next_billing_date = period_end.date()
    subscription.external_provider = "KONNECT"
    subscription.cancel_at_period_end = 0
    subscription.save(ignore_permissions=True)
    payment.billing_period_start = period_start
    payment.billing_period_end = period_end
    clear_request_cache()


def reconcile_provider_payment(provider_payment_id, expected_business=None):
    if not PAYMENT_REF_PATTERN.fullmatch(provider_payment_id or ""):
        return {"status":"UNKNOWN"}
    savepoint = f"billing_reconcile_{frappe.generate_hash(length=8)}"
    frappe.db.savepoint(savepoint)
    payment = _lock_payment(provider_payment_id)
    if not payment:
        return {"status":"UNKNOWN"}
    if expected_business and payment.business != expected_business:
        frappe.throw(_("Billing payment is outside your Business."), frappe.PermissionError)
    if payment.status == "SUCCEEDED":
        return {"payment":payment.name, "status":"SUCCEEDED"}
    try:
        details = _provider(payment.provider).get_payment_status(provider_payment_id)
    except Exception:
        frappe.clear_messages()
        payment.last_reconciled_at = now_datetime(); payment.reconciliation_count = (payment.reconciliation_count or 0)+1
        payment.last_reconciliation_message = "Provider unavailable; payment remains pending"
        payment.save(ignore_permissions=True)
        return {"payment":payment.name,"status":payment.status}
    payment.last_reconciled_at = now_datetime(); payment.reconciliation_count = (payment.reconciliation_count or 0)+1
    payment.provider_status = details.get("provider_status"); payment.provider_response = _safe_metadata(details)
    if details.get("normalized_status") == "SUCCEEDED":
        mismatch = (
            int(details.get("amount_minor") or -1) != tnd_to_millimes(payment.amount)
            or details.get("currency") != payment.currency
            or details.get("order_id") != payment.internal_order_id
            or (details.get("receiver_wallet_id") and details.get("receiver_wallet_id") != _provider(payment.provider).wallet_id)
            or details.get("transaction_success") is False
        )
        if mismatch:
            payment.last_reconciliation_message = "Provider confirmation did not match expected billing data"
            payment.save(ignore_permissions=True)
            return {"payment":payment.name,"status":"PENDING"}
        paid_at = now_datetime()
        _activate(payment, paid_at)
        payment.status="SUCCEEDED"; payment.paid_at=paid_at; payment.last_reconciliation_message="Payment verified and applied"
    elif details.get("normalized_status") in {"FAILED","EXPIRED","CANCELLED"}:
        payment.status=details["normalized_status"]; payment.failed_at=now_datetime(); payment.last_reconciliation_message="Provider reported non-successful payment"
    else:
        payment.last_reconciliation_message="Provider payment remains pending"
    payment.save(ignore_permissions=True)
    return {"payment":payment.name,"status":payment.status}


def reconcile_owner_payment(payment_name):
    business = _owner_business()
    payment = frappe.get_doc("CreditFlow Billing Payment", payment_name)
    if payment.business != business:
        frappe.throw(_("Billing payment is outside your Business."), frappe.PermissionError)
    if not payment.provider_payment_id:
        return {"payment":payment.name,"status":payment.status}
    return reconcile_provider_payment(payment.provider_payment_id, business)


def request_cancellation():
    business = _owner_business(); subscription=get_subscription(business)
    if not subscription or subscription.status != "ACTIVE":
        frappe.throw(_("There is no active subscription to cancel."))
    subscription.cancel_at_period_end=1; subscription.cancelled_at=now_datetime(); subscription.save(ignore_permissions=True)
    clear_request_cache()
    return {"ok":True,"access_through":subscription.current_period_end}


def billing_history(business):
    return frappe.get_all("CreditFlow Billing Payment", filters={"business":business}, fields=["name","plan","amount","currency","billing_cycle","status","created_at","paid_at","checkout_url"], order_by="creation desc", limit=50)
