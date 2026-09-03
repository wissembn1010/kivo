import frappe
from frappe import _

from creditflow.permissions import get_user_business, has_cross_business_access
from creditflow.subscription import get_subscription_summary


def _banner(summary):
    state = summary["access_state"]
    if state == "TRIAL":
        days = summary.get("days_remaining") or 0
        return _("{0} days left in your trial").format(days)
    if state == "PAST_DUE":
        return _("Payment issue — Kivo is currently read-only")
    if state == "EXPIRED":
        return _("Trial/subscription expired — Kivo is read-only")
    if state == "CANCELLED":
        return _("Subscription cancelled — Kivo is read-only")
    return None


def extend_bootinfo(bootinfo):
    user = frappe.session.user
    roles = set(frappe.get_roles(user))
    if user in {"Guest", "Administrator"} or not {"OWNER", "STAFF"}.intersection(roles):
        return
    business = get_user_business(user)
    if not business:
        return
    summary = get_subscription_summary(business)
    public = {
        "access_state": summary["access_state"],
        "can_write": summary["can_write"],
        "banner": _banner(summary),
        "protected_doctypes": sorted(__import__("creditflow.permissions", fromlist=["DEFAULTED_DOCTYPES"]).DEFAULTED_DOCTYPES | {"Business", "Customer", "Product", "Supplier", "Credit Transaction", "Stock Movement"}),
    }
    if "OWNER" in roles:
        public.update({"plan": summary["plan"], "status": summary["status"], "subscription_route": "/creditflow-subscription"})
    bootinfo.creditflow_saas = public


def get_owner_subscription_summary():
    if frappe.session.user == "Guest" or "OWNER" not in frappe.get_roles():
        frappe.throw(_("Only a Business OWNER can view subscription details."), frappe.PermissionError)
    business = get_user_business()
    if not business:
        frappe.throw(_("Your user is not assigned to a Kivo Business."), frappe.PermissionError)
    return get_subscription_summary(business)


@frappe.whitelist()
def subscription_summary():
    return get_owner_subscription_summary()
