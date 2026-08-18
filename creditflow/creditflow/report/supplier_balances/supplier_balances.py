from decimal import Decimal, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.utils import cint

from creditflow.permissions import get_user_business, has_cross_business_access


MONEY_QUANTUM = Decimal("0.001")


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
			"fieldname": "supplier",
			"label": _("Supplier"),
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 140,
		},
		{"fieldname": "phone", "label": _("Phone"), "fieldtype": "Data", "width": 130},
		{
			"fieldname": "total_purchases",
			"label": _("Total Purchases"),
			"fieldtype": "Currency",
			"precision": 3,
			"width": 150,
		},
		{
			"fieldname": "total_payments",
			"label": _("Total Payments"),
			"fieldtype": "Currency",
			"precision": 3,
			"width": 150,
		},
		{
			"fieldname": "returns",
			"label": _("Returns"),
			"fieldtype": "Currency",
			"precision": 3,
			"width": 120,
		},
		{
			"fieldname": "outstanding_balance",
			"label": _("Outstanding Balance"),
			"fieldtype": "Currency",
			"precision": 3,
			"width": 170,
		},
	]


def get_data(filters, business):
	conditions = []
	values = {}
	if business:
		conditions.append("supplier.business = %(business)s")
		values["business"] = business
	if filters.get("supplier"):
		conditions.append("supplier.name = %(supplier)s")
		values["supplier"] = filters.supplier

	where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
	balance_expression = """
		COALESCE(SUM(CASE WHEN ledger.direction = 'DEBIT' THEN ledger.amount ELSE 0 END), 0)
		- COALESCE(SUM(CASE WHEN ledger.direction = 'CREDIT' THEN ledger.amount ELSE 0 END), 0)
	"""
	if not cint(filters.get("show_zero_balances")):
		having_clause = f"HAVING ({balance_expression}) != 0"
	else:
		having_clause = ""

	rows = frappe.db.sql(
		f"""
		SELECT
			supplier.name AS supplier,
			supplier.supplier_name,
			supplier.phone,
			supplier.business,
			COALESCE(SUM(CASE WHEN ledger.direction = 'DEBIT' THEN ledger.amount ELSE 0 END), 0) AS total_purchases,
			COALESCE(SUM(CASE WHEN ledger.transaction_type = 'SUPPLIER_PAYMENT' AND ledger.direction = 'CREDIT' THEN ledger.amount ELSE 0 END), 0) AS total_payments,
			COALESCE(SUM(CASE WHEN ledger.transaction_type = 'RETURN' AND ledger.direction = 'CREDIT' THEN ledger.amount ELSE 0 END), 0) AS returns
		FROM `tabSupplier` supplier
		LEFT JOIN `tabSupplier Transaction` ledger
			ON ledger.supplier = supplier.name
			AND ledger.business = supplier.business
			AND ledger.docstatus = 1
		{where_clause}
		GROUP BY
			supplier.name,
			supplier.supplier_name,
			supplier.phone,
			supplier.business
		{having_clause}
		ORDER BY ({balance_expression}) DESC, supplier.supplier_name ASC
		""",
		values,
		as_dict=True,
	)

	for row in rows:
		row.total_purchases = _money(row.total_purchases)
		row.total_payments = _money(row.total_payments)
		row.returns = _money(row.returns)
		row.outstanding_balance = _money(row.total_purchases - row.total_payments - row.returns)
	return rows


def _money(value):
	return Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
