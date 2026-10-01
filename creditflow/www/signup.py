import frappe
from frappe import _


def get_context(context):
    context.no_cache = 1
    context.title = _('Start your Kivo trial')
    context.countries = frappe.get_all("Country", pluck="name", order_by="name asc")
