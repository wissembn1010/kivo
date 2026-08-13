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
			"fieldname": "customer",
			"label": _("Customer"),
			"fieldtype": "Link",
			"options": "Customer",
			"width": 140,
		},
		{
			"fieldname": "customer_name",
			"label": _("Customer Name"),
			"fieldtype": "Data",
			"width": 200,
		},
		{"fieldname": "phone", "label": _("Phone"), "fieldtype": "Data", "width": 130},
		{
			"fieldname": "business",
			"label": _("Business"),
			"fieldtype": "Link",
			"options": "Business",
			"width": 140,
		},
		{
			"fieldname": "total_debit",
			"label": _("Total Debit"),
			"fieldtype": "Currency",
			"precision": 3,
			"width": 130,
		},
		{
			"fieldname": "total_credit",
			"label": _("Total Credit"),
			"fieldtype": "Currency",
			"precision": 3,
			"width": 130,
		},
		{
			"fieldname": "outstanding_balance",
			"label": _("Outstanding Balance"),
			"fieldtype": "Currency",
			"precision": 3,
			"width": 160,
		},
	]


def get_data(filters, business):
	conditions = []
	values = {}
	if business:
		conditions.append("customer.business = %(business)s")
		values["business"] = business
	if filters.get("customer"):
		conditions.append("customer.name = %(customer)s")
		values["customer"] = filters.customer

	where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
	balance_expression = """
		COALESCE(SUM(CASE WHEN ledger.direction = 'DEBIT' THEN ledger.amount ELSE 0 END), 0)
		- COALESCE(SUM(CASE WHEN ledger.direction = 'CREDIT' THEN ledger.amount ELSE 0 END), 0)
	"""
	having_clause = ""
	if not cint(filters.get("show_zero_balances")):
		having_clause = f"HAVING ({balance_expression}) != 0"

	rows = frappe.db.sql(
		f"""
		SELECT
			customer.name AS customer,
			customer.customer_name,
			customer.phone,
			customer.business,
			COALESCE(SUM(CASE WHEN ledger.direction = 'DEBIT' THEN ledger.amount ELSE 0 END), 0)
				AS total_debit,
			COALESCE(SUM(CASE WHEN ledger.direction = 'CREDIT' THEN ledger.amount ELSE 0 END), 0)
				AS total_credit
		FROM `tabCustomer` customer
		LEFT JOIN `tabCredit Transaction` ledger
			ON ledger.customer = customer.name
			AND ledger.business = customer.business
			AND ledger.docstatus = 1
		{where_clause}
		GROUP BY
			customer.name,
			customer.customer_name,
			customer.phone,
			customer.business
		{having_clause}
		ORDER BY ({balance_expression}) DESC, customer.customer_name ASC
		""",
		values,
		as_dict=True,
	)

	for row in rows:
		row.total_debit = _money(row.total_debit)
		row.total_credit = _money(row.total_credit)
		row.outstanding_balance = _money(row.total_debit - row.total_credit)
	return rows


def _money(value):
	return Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
