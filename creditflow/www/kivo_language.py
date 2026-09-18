import frappe

from creditflow.i18n import get_language_context


def get_context(context):
	context.no_cache = 1
	context.kivo_return_url = "/login" if frappe.session.user == "Guest" else "/desk/kivo"
	# Frappe's standard navbar reads this value for its Home/brand link.
	context.home_page = context.kivo_return_url
	context.update(get_language_context())
