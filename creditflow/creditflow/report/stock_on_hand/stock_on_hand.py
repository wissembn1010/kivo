from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import cint

from creditflow.permissions import get_user_business, has_cross_business_access


def execute(filters=None):
	filters = frappe._dict(filters or {})
	business = _get_business_filter(filters)
	columns = get_columns()
	data = get_data(filters, business)
	return columns, data


def _get_business_filter(filters):
	user = frappe.session.user
	if has_cross_business_access(user):
		return filters.get("business")

	assigned_business = get_user_business(user)
	if not assigned_business:
		frappe.throw(
			_("Your user is not assigned to a CreditFlow Business."),
			frappe.PermissionError,
		)
	if filters.get("business") and filters.business != assigned_business:
		frappe.throw(
			_("You can only report on your assigned CreditFlow Business."),
			frappe.PermissionError,
		)
	return assigned_business


def get_columns():
	return [
		{
			"fieldname": "product",
			"label": _("Product"),
			"fieldtype": "Link",
			"options": "Product",
			"width": 140,
		},
		{"fieldname": "reference", "label": _("Reference"), "fieldtype": "Data", "width": 140},
		{
			"fieldname": "product_name",
			"label": _("Product Name"),
			"fieldtype": "Data",
			"width": 200,
		},
		{"fieldname": "unit", "label": _("Unit"), "fieldtype": "Data", "width": 90},
		{
			"fieldname": "business",
			"label": _("Business"),
			"fieldtype": "Link",
			"options": "Business",
			"width": 140,
		},
		{"fieldname": "total_in", "label": _("Total IN"), "fieldtype": "Float", "width": 120},
		{"fieldname": "total_out", "label": _("Total OUT"), "fieldtype": "Float", "width": 120},
		{
			"fieldname": "stock_on_hand",
			"label": _("Stock On Hand"),
			"fieldtype": "Float",
			"width": 130,
		},
	]


def get_data(filters, business):
	conditions = []
	values = {}
	if business:
		conditions.append("product.business = %(business)s")
		values["business"] = business
	if filters.get("product"):
		conditions.append("product.name = %(product)s")
		values["product"] = filters.product

	where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
	stock_expression = """
		COALESCE(SUM(CASE WHEN movement.direction = 'IN' THEN movement.quantity ELSE 0 END), 0)
		- COALESCE(SUM(CASE WHEN movement.direction = 'OUT' THEN movement.quantity ELSE 0 END), 0)
	"""
	if cint(filters.get("show_negative_stock_only")):
		having_clause = f"HAVING ({stock_expression}) < 0"
	elif not cint(filters.get("show_zero_stock")):
		having_clause = f"HAVING ({stock_expression}) != 0"
	else:
		having_clause = ""

	rows = frappe.db.sql(
		f"""
		SELECT
			product.name AS product,
			product.reference,
			product.product_name,
			product.primary_unit AS unit,
			product.business,
			COALESCE(SUM(CASE WHEN movement.direction = 'IN' THEN movement.quantity ELSE 0 END), 0)
				AS total_in,
			COALESCE(SUM(CASE WHEN movement.direction = 'OUT' THEN movement.quantity ELSE 0 END), 0)
				AS total_out
		FROM `tabProduct` product
		LEFT JOIN `tabStock Movement` movement
			ON movement.product = product.name
			AND movement.business = product.business
			AND movement.docstatus = 1
		{where_clause}
		GROUP BY
			product.name,
			product.reference,
			product.product_name,
			product.primary_unit,
			product.business
		{having_clause}
		ORDER BY product.product_name ASC, product.name ASC
		""",
		values,
		as_dict=True,
	)

	for row in rows:
		row.total_in = Decimal(str(row.total_in or 0))
		row.total_out = Decimal(str(row.total_out or 0))
		row.stock_on_hand = row.total_in - row.total_out
	return rows
