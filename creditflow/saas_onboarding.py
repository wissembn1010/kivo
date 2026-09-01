import frappe
from frappe import _
from frappe.utils import now_datetime

from creditflow.permissions import get_user_business, has_cross_business_access


def get_creditflow_home_page(user):
    """Route only explicitly pending self-service owners; historical blank states are untouched."""
    if not user or user in {"Guest", "Administrator"} or "OWNER" not in frappe.get_roles(user):
        return None
    business = get_user_business(user)
    if business and frappe.db.get_value("Business", business, "onboarding_status") == "PROFILE_PENDING":
        return "creditflow-onboarding"
    return None


def get_onboarding_business():
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in to continue."), frappe.PermissionError)
    if "OWNER" not in frappe.get_roles() and not has_cross_business_access():
        frappe.throw(_("Only a Business OWNER can complete onboarding."), frappe.PermissionError)
    business = get_user_business()
    if not business:
        frappe.throw(_("Your user is not assigned to a CreditFlow Business."), frappe.PermissionError)
    return frappe.get_doc("Business", business)


@frappe.whitelist(methods=["POST"])
def complete_business_profile(business_name=None, phone=None, country=None, address=None, tax_identifier=None, commercial_registration=None, skip=0, **unsupported):
    if unsupported:
        frappe.throw(_("Unsupported onboarding fields."))
    business = get_onboarding_business()
    if business.onboarding_status != "PROFILE_PENDING":
        return {"ok": True, "redirect_to": "/app/creditflow"}
    if not int(skip or 0):
        business_name = (business_name or "").strip()
        if not business_name:
            frappe.throw(_("Business name is required."))
        country = (country or "").strip() or None
        if country and not frappe.db.exists("Country", country):
            frappe.throw(_("Country is invalid."))
        business.business_name = business_name
        business.phone = (phone or "").strip() or None
        business.country = country
        business.address = (address or "").strip() or None
        business.tax_identifier = (tax_identifier or "").strip() or None
        business.commercial_registration = (commercial_registration or "").strip() or None
    business.onboarding_status = "COMPLETED"
    business.onboarding_completed_at = now_datetime()
    business.save()
    return {"ok": True, "redirect_to": "/app/creditflow"}
