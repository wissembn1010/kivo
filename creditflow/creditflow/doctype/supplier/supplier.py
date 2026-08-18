from decimal import Decimal, ROUND_HALF_UP

import frappe
from frappe.model.document import Document


MONEY_QUANTUM = Decimal("0.001")


class Supplier(Document):
	@frappe.whitelist()
	def get_ledger_balance(self):
		if self.is_new() or not self.business:
			return "0.000"

		result = frappe.db.sql(
			"""
			SELECT COALESCE(
				SUM(
					CASE
						WHEN direction = 'DEBIT' THEN amount
						WHEN direction = 'CREDIT' THEN -amount
						ELSE 0
					END
				),
				0
			)
			FROM `tabSupplier Transaction`
			WHERE supplier = %s
			  AND business = %s
			  AND docstatus = 1
			""",
			(self.name, self.business),
		)

		balance = result[0][0] if result else 0
		return str(Decimal(str(balance or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))

	@frappe.whitelist()
	def get_total_purchases(self):
		return self._aggregate_supplier_transaction("PURCHASE", "DEBIT")

	@frappe.whitelist()
	def get_total_payments(self):
		return self._aggregate_supplier_transaction("SUPPLIER_PAYMENT", "CREDIT")

	@frappe.whitelist()
	def get_total_returns(self):
		return self._aggregate_supplier_transaction("RETURN", "CREDIT")

	@frappe.whitelist()
	def get_last_purchase(self):
		return self._latest_transaction("PURCHASE")

	@frappe.whitelist()
	def get_last_payment(self):
		return self._latest_transaction("SUPPLIER_PAYMENT")

	def _aggregate_supplier_transaction(self, transaction_type, direction):
		if self.is_new() or not self.business:
			return "0.000"
		result = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(amount), 0)
			FROM `tabSupplier Transaction`
			WHERE supplier = %s
			  AND business = %s
			  AND transaction_type = %s
			  AND direction = %s
			  AND docstatus = 1
			""",
			(self.name, self.business, transaction_type, direction),
		)
		amount = result[0][0] if result else 0
		return str(Decimal(str(amount or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))

	def _latest_transaction(self, transaction_type):
		if self.is_new() or not self.business:
			return None
		return frappe.db.get_value(
			"Supplier Transaction",
			{
				"supplier": self.name,
				"business": self.business,
				"transaction_type": transaction_type,
				"docstatus": 1,
			},
			"transaction_date",
			order_by="transaction_date desc, creation desc",
		)
