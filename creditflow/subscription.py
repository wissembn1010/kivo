import json
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, get_datetime, now_datetime

from creditflow.permissions import get_user_business, has_cross_business_access

DEFAULT_TRIAL_DAYS = 14
DEFAULT_TRIAL_PLAN = "STARTER"
WRITABLE_STATES = {"TRIAL", "ACTIVE", "LEGACY_ACCESS", "ADMIN_OVERRIDE"}
READ_ONLY_MESSAGE = _("Your Kivo subscription is currently read-only. Your existing data is safe.")


def get_default_trial_plan():
    return frappe.conf.get("creditflow_default_trial_plan") or DEFAULT_TRIAL_PLAN


def clear_request_cache():
    if hasattr(frappe.local, "creditflow_subscription_cache"):
        del frappe.local.creditflow_subscription_cache


def _cache():
    if not hasattr(frappe.local, "creditflow_subscription_cache"):
        frappe.local.creditflow_subscription_cache = {}
    return frappe.local.creditflow_subscription_cache


def _authorized_business(business=None):
    if has_cross_business_access():
        if not business:
            frappe.throw(_("Business is required for administrative subscription access."))
        return business
    assigned = get_user_business()
    if not assigned:
        frappe.throw(_("Your user is not assigned to a Kivo Business."), frappe.PermissionError)
    if business and business != assigned:
        frappe.throw(_("You can only access the subscription for your assigned Business."), frappe.PermissionError)
    return assigned


def get_subscription(business=None):
    business = _authorized_business(business)
    key = ("subscription", business)
    if key not in _cache():
        name = frappe.db.get_value("CreditFlow Subscription", {"business": business}, "name")
        _cache()[key] = frappe.get_doc("CreditFlow Subscription", name) if name else None
    return _cache()[key]


def get_plan(business=None):
    business = _authorized_business(business)
    key = ("plan", business)
    if key not in _cache():
        subscription = get_subscription(business)
        _cache()[key] = frappe.get_doc("CreditFlow Plan", subscription.plan) if subscription else None
    return _cache()[key]


def get_access_state(business=None, at=None):
    business = _authorized_business(business)
    subscription = get_subscription(business)
    if not subscription:
        return "LEGACY_ACCESS"
    now = get_datetime(at or now_datetime())
    if subscription.status == "TRIAL":
        return "TRIAL" if subscription.trial_start and subscription.trial_end and subscription.trial_start <= now < subscription.trial_end else "EXPIRED"
    if subscription.status == "ACTIVE" and subscription.current_period_end and get_datetime(subscription.current_period_end) <= now:
        return "CANCELLED" if subscription.cancel_at_period_end else "EXPIRED"
    return subscription.status


def is_trial_active(business=None, at=None):
    return get_access_state(business, at) == "TRIAL"


def is_subscription_active(business=None, at=None):
    return get_access_state(business, at) in WRITABLE_STATES


def can_write(business=None, at=None):
    return get_access_state(business, at) in WRITABLE_STATES


def require_write_access(business=None):
    business = _authorized_business(business)
    if not can_write(business):
        frappe.throw(READ_ONLY_MESSAGE, frappe.PermissionError)
    return get_access_state(business)


def _typed_value(row):
    value_type, value = row.value_type or "TEXT", row.value
    try:
        if value_type == "BOOLEAN":
            text = str(value or "").strip().lower()
            return {"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False}[text]
        if value_type == "INTEGER":
            return int(str(value).strip())
        if value_type == "DECIMAL":
            number = Decimal(str(value))
            return number if number.is_finite() else None
        if value_type == "JSON":
            return json.loads(value or "null")
    except (InvalidOperation, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None
    return value


def get_entitlement(business, feature, default=None):
    business = _authorized_business(business)
    if get_access_state(business) == "LEGACY_ACCESS":
        return default
    plan = get_plan(business)
    if not plan or not plan.enabled:
        return default
    key = (feature or "").strip().lower()
    row = next((item for item in plan.entitlements if item.entitlement_key == key), None)
    return _typed_value(row) if row else default


def can_use_feature(business, feature, default=False):
    business = _authorized_business(business)
    state = get_access_state(business)
    if state == "LEGACY_ACCESS":
        return True
    if state not in WRITABLE_STATES:
        return False
    value = get_entitlement(business, feature, default)
    return bool(value) if isinstance(value, bool) else value is not None and value != 0


def require_feature(business, feature):
    business = _authorized_business(business)
    require_write_access(business)
    if not can_use_feature(business, feature):
        frappe.throw(_("Your Kivo plan does not include entitlement: {0}").format(feature), frappe.PermissionError)
    return get_entitlement(business, feature, True if get_access_state(business) == "LEGACY_ACCESS" else None)


def require_active_subscription(business=None):
    business = _authorized_business(business)
    require_write_access(business)
    return get_subscription(business)


def require_entitlement(business, feature):
    return require_feature(business, feature)


def get_subscription_summary(business=None):
    business = _authorized_business(business)
    subscription = get_subscription(business)
    state = get_access_state(business)
    plan = get_plan(business) if subscription else None
    days_remaining = None
    if state == "TRIAL" and subscription.trial_end:
        days_remaining = max(0, date_diff(subscription.trial_end, now_datetime()))
    return {
        "business": business,
        "access_state": state,
        "can_write": state in WRITABLE_STATES,
        "plan": plan.plan_name if plan else ("Legacy" if state == "LEGACY_ACCESS" else None),
        "plan_code": plan.plan_code if plan else None,
        "status": subscription.status if subscription else "LEGACY_ACCESS",
        "trial_end": subscription.trial_end if subscription else None,
        "days_remaining": days_remaining,
        "current_period_end": subscription.current_period_end if subscription else None,
        "entitlements": {row.entitlement_key: _typed_value(row) for row in (plan.entitlements if plan else [])},
    }


def create_trial_subscription(business, plan=None, trial_days=DEFAULT_TRIAL_DAYS):
    business = _authorized_business(business)
    plan = plan or get_default_trial_plan()
    if get_subscription(business):
        frappe.throw(_("Business {0} already has a subscription.").format(business))
    start = now_datetime()
    doc = frappe.get_doc({"doctype":"CreditFlow Subscription","business":business,"plan":plan,"status":"TRIAL","trial_start":start,"trial_end":add_days(start,trial_days)}).insert(ignore_permissions=True)
    clear_request_cache()
    return doc
