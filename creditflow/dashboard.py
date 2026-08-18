from decimal import Decimal, ROUND_HALF_UP

import frappe
from frappe import _

from creditflow.permissions import get_user_business, has_cross_business_access


MONEY_QUANTUM = Decimal("0.001")


def _business_scope(alias):
	user = frappe.session.user
	if has_cross_business_access(user):
		return "", {}

	business = get_user_business(user)
	if not business:
		return " AND 1 = 0", {}

	return f" AND {alias}.business = %(business)s", {"business": business}


def _money_card(value, route):
	value = Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
	return {
		"value": value,
		"fieldtype": "Currency",
		"precision": 3,
		"route": route,
	}


@frappe.whitelist()
def total_customer_debt(filters=None):
	scope, values = _business_scope("ledger")
	result = frappe.db.sql(
		f"""
		SELECT COALESCE(
			SUM(
				CASE
					WHEN ledger.direction = 'DEBIT' THEN ledger.amount
					WHEN ledger.direction = 'CREDIT' THEN -ledger.amount
					ELSE 0
				END
			),
			0
		)
		FROM `tabCredit Transaction` ledger
		WHERE ledger.docstatus = 1
		{scope}
		""",
		values,
	)[0][0]
	return _money_card(result, ["query-report", "Customer Balances"])


@frappe.whitelist()
def total_supplier_debt(filters=None):
	scope, values = _business_scope("ledger")
	result = frappe.db.sql(
		f"""
		SELECT COALESCE(
			SUM(
				CASE
					WHEN ledger.direction = 'DEBIT' THEN ledger.amount
					WHEN ledger.direction = 'CREDIT' THEN -ledger.amount
					ELSE 0
				END
			),
			0
		)
		FROM `tabSupplier Transaction` ledger
		WHERE ledger.docstatus = 1
		{scope}
		""",
		values,
	)[0][0]
	return _money_card(result, ["query-report", "Supplier Balances"])


def _today_total(doctype, amount_field):
	scope, values = _business_scope("document")
	values["today"] = frappe.utils.today()
	result = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(document.`{amount_field}`), 0)
		FROM `tab{doctype}` document
		WHERE document.docstatus = 1
		  AND document.business_date = %(today)s
		{scope}
		""",
		values,
	)[0][0]
	return _money_card(result, ["List", doctype])


@frappe.whitelist()
def sales_today(filters=None):
	return _today_total("Sale", "total_amount")


@frappe.whitelist()
def payments_today(filters=None):
	return _today_total("Payment", "amount")


@frappe.whitelist()
def supplier_payments_today(filters=None):
	return _today_total("Supplier Payment", "amount")


@frappe.whitelist()
def purchases_today(filters=None):
	return _today_total("Purchase", "total_amount")


@frappe.whitelist()
def negative_stock_products(filters=None):
	scope, values = _business_scope("movement")
	result = frappe.db.sql(
		f"""
		SELECT COUNT(*)
		FROM (
			SELECT movement.business, movement.product
			FROM `tabStock Movement` movement
			WHERE movement.docstatus = 1
			{scope}
			GROUP BY movement.business, movement.product
			HAVING SUM(
				CASE
					WHEN movement.direction = 'IN' THEN movement.quantity
					WHEN movement.direction = 'OUT' THEN -movement.quantity
					ELSE 0
				END
			) < 0
		) negative_stock
		""",
		values,
	)[0][0]
	return {
		"value": int(result or 0),
		"fieldtype": "Int",
		"route": ["query-report", "Stock On Hand"],
		"route_options": {"show_negative_stock_only": 1},
	}
