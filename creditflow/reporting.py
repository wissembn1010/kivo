from decimal import Decimal, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.utils import add_days, get_first_day, get_last_day, getdate, today

from creditflow.permissions import get_user_business, has_cross_business_access

MONEY = Decimal("0.001")


def money(value):
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def business_filter(filters):
    filters = frappe._dict(filters or {})
    if has_cross_business_access():
        return filters.get("business")
    assigned = get_user_business()
    if not assigned:
        frappe.throw(_("Your user is not assigned to a Kivo Business."), frappe.PermissionError)
    if filters.get("business") and filters.business != assigned:
        frappe.throw(_("You can only report on your assigned Kivo Business."), frappe.PermissionError)
    return assigned


def apply_period(filters):
    period = filters.get("period")
    current = getdate(today())
    if period == "Today":
        filters.from_date = filters.to_date = current
    elif period == "This Week":
        filters.from_date = add_days(current, -current.weekday())
        filters.to_date = current
    elif period == "This Month":
        filters.from_date = get_first_day(current)
        filters.to_date = get_last_day(current)
    return filters

def date_conditions(filters, field, values):
    conditions = []
    if filters.get("from_date"):
        conditions.append(f"{field} >= %(from_date)s"); values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append(f"{field} <= %(to_date)s"); values["to_date"] = filters.to_date
    return conditions


def money_col(fieldname, label, width=125):
    return {"fieldname":fieldname,"label":_(label),"fieldtype":"Currency","precision":3,"width":width}
