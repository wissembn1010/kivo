import frappe

from creditflow.tej import official_country_codes, official_operation_codes


def execute():
	for code, description in official_operation_codes().items():
		if not frappe.db.exists("TEJ Operation Code", code):
			frappe.get_doc({"doctype": "TEJ Operation Code", "code": code, "description": description}).insert(ignore_permissions=True)
	for code, country_name in official_country_codes().items():
		if not frappe.db.exists("TEJ Country Code", code):
			frappe.get_doc({"doctype": "TEJ Country Code", "code": code, "country_name": country_name}).insert(ignore_permissions=True)
