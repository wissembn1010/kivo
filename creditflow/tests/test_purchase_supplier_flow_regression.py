from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.creditflow.report.purchases_report.purchases_report import (
	execute as purchases_report,
)
from creditflow.creditflow.report.stock_on_hand.stock_on_hand import (
	execute as stock_on_hand,
)
from creditflow.creditflow.report.supplier_balances.supplier_balances import (
	execute as supplier_balances,
)
from creditflow.creditflow.report.supplier_statement.supplier_statement import (
	execute as supplier_statement,
)


class IntegrationTestPurchaseSupplierFlowRegression(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Purchase Regression Business {suffix}"}
		).insert()
		self.supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": self.business.name,
				"supplier_name": f"Purchase Regression Supplier {suffix}",
			}
		).insert()
		self.product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business.name,
				"product_name": f"Purchase Regression Product {suffix}",
				"reference": f"PUR-REG-{suffix}",
				"primary_unit": "UNIT",
			}
		).insert()
		self.post_opening_stock("10")

	def tearDown(self):
		frappe.set_user("Administrator")

	def post_opening_stock(self, quantity):
		movement = frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business.name,
				"product": self.product.name,
				"direction": "IN",
				"quantity": quantity,
				"movement_reason": "OPENING_STOCK",
				"business_date": "2026-08-19",
			}
		).insert()
		movement.submit()
		return movement

	def make_purchase(
		self,
		*,
		business_date="2026-08-20",
		supplier=None,
		product=None,
		payment_status="CREDIT",
		paid_amount=None,
		payment_method=None,
	):
		values = {
			"doctype": "Purchase",
			"business": self.business.name,
			"supplier": supplier or self.supplier.name,
			"business_date": business_date,
			"payment_status": payment_status,
			"items": [
				{
					"product": product or self.product.name,
					"quantity": "2",
					"unit_cost": "50",
				}
			],
		}
		if paid_amount is not None:
			values["paid_amount"] = paid_amount
		if payment_method is not None:
			values["payment_method"] = payment_method
		return frappe.get_doc(values)

	def submit_purchase(self, **kwargs):
		purchase = self.make_purchase(**kwargs).insert()
		purchase.submit()
		return purchase

	def submit_reversal(self, purchase, business_date="2026-08-24"):
		reversal = frappe.get_doc(
			{
				"doctype": "Purchase Reversal",
				"business": self.business.name,
				"original_purchase": purchase.name,
				"business_date": business_date,
				"reason": "Purchase/supplier regression test",
			}
		).insert()
		reversal.submit()
		return reversal

	def automatic_payments(self, purchase):
		return frappe.get_all(
			"Supplier Payment",
			filters={"source_purchase": purchase.name, "docstatus": 1},
			fields=["name", "amount", "payment_method", "business_date", "source_purchase"],
		)

	def purchase_transaction(self, purchase):
		return frappe.db.get_value(
			"Supplier Transaction",
			{"source_purchase": purchase.name, "docstatus": 1},
			["name", "transaction_type", "direction", "amount"],
			as_dict=True,
		)

	def payment_transaction(self, payment_name):
		return frappe.db.get_value(
			"Supplier Transaction",
			{"source_supplier_payment": payment_name, "docstatus": 1},
			["name", "transaction_type", "direction", "amount"],
			as_dict=True,
		)

	def stock_balance(self):
		value = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN quantity ELSE -quantity END), 0)
			FROM `tabStock Movement`
			WHERE business=%s AND product=%s AND docstatus=1
			""",
			(self.business.name, self.product.name),
		)[0][0]
		return Decimal(str(value))

	def supplier_balance(self):
		return Decimal(self.supplier.get_ledger_balance())

	def statement(self):
		_, rows, _, _, _, skip_total_row = supplier_statement(
			{"supplier": self.supplier.name}
		)
		return rows, skip_total_row

	def supplier_balance_row(self):
		_, rows = supplier_balances(
			{
				"business": self.business.name,
				"supplier": self.supplier.name,
				"show_zero_balances": 1,
			}
		)
		return rows[0]

	def test_01_credit_purchase_posts_stock_debt_and_no_payment(self):
		purchase = self.submit_purchase(payment_status="CREDIT")
		transaction = self.purchase_transaction(purchase)
		rows, _ = self.statement()
		statement_row = next(row for row in rows[:-1] if row.reference == purchase.name)

		self.assertEqual(Decimal(str(purchase.total_amount)), Decimal("100.000"))
		self.assertEqual(self.stock_balance(), Decimal("12"))
		self.assertEqual(transaction.transaction_type, "PURCHASE")
		self.assertEqual(transaction.direction, "DEBIT")
		self.assertEqual(Decimal(str(transaction.amount)), Decimal("100.000"))
		self.assertEqual(self.supplier_balance(), Decimal("100.000"))
		self.assertEqual(statement_row.debit, Decimal("100.000"))
		self.assertEqual(statement_row.running_balance, Decimal("100.000"))
		self.assertFalse(self.automatic_payments(purchase))
		self.assertEqual(self.supplier.get_last_purchase(), frappe.utils.getdate("2026-08-20"))
		self.assertIsNone(self.supplier.get_last_payment())

	def test_02_paid_purchase_posts_one_linked_payment_without_new_debt(self):
		baseline = self.submit_purchase(payment_status="CREDIT", business_date="2026-08-20")
		balance_before = self.supplier_balance()
		paid = self.submit_purchase(
			payment_status="PAID",
			payment_method="CASH",
			business_date="2026-08-21",
		)
		payments = self.automatic_payments(paid)
		purchase_entry = self.purchase_transaction(paid)
		payment_entry = self.payment_transaction(payments[0].name)
		rows, _ = self.statement()

		self.assertEqual(self.stock_balance(), Decimal("14"))
		self.assertEqual(Decimal(str(purchase_entry.amount)), Decimal("100.000"))
		self.assertEqual(purchase_entry.direction, "DEBIT")
		self.assertEqual(len(payments), 1)
		self.assertEqual(Decimal(str(payments[0].amount)), Decimal("100.000"))
		self.assertEqual(payment_entry.direction, "CREDIT")
		self.assertEqual(Decimal(str(payment_entry.amount)), Decimal("100.000"))
		self.assertEqual(self.supplier_balance(), balance_before)
		self.assertEqual(paid.automatic_supplier_payment, payments[0].name)
		self.assertEqual(payments[0].source_purchase, paid.name)
		self.assertEqual(self.supplier_balance_row().outstanding_balance, Decimal("100.000"))
		self.assertEqual(self.supplier.get_last_payment(), frappe.utils.getdate("2026-08-21"))
		self.assertEqual(
			[row.reference for row in rows[:-1]],
			[baseline.name, paid.name, payments[0].name],
		)

	def test_03_partial_purchase_posts_remaining_debt_and_linked_payment(self):
		partial = self.submit_purchase(
			payment_status="PARTIAL",
			paid_amount="40",
			payment_method="CASH",
			business_date="2026-08-22",
		)
		payment = self.automatic_payments(partial)[0]
		purchase_entry = self.purchase_transaction(partial)
		payment_entry = self.payment_transaction(payment.name)
		rows, _ = self.statement()

		self.assertEqual(self.stock_balance(), Decimal("12"))
		self.assertEqual(Decimal(str(purchase_entry.amount)), Decimal("100.000"))
		self.assertEqual(Decimal(str(payment_entry.amount)), Decimal("40.000"))
		self.assertEqual(self.supplier_balance(), Decimal("60.000"))
		self.assertEqual(self.supplier_balance_row().outstanding_balance, Decimal("60.000"))
		self.assertEqual(rows[-1].running_balance, Decimal("60.000"))
		self.assertEqual(partial.automatic_supplier_payment, payment.name)
		self.assertEqual(payment.source_purchase, partial.name)
		self.assertEqual(self.supplier.get_last_payment(), frappe.utils.getdate("2026-08-22"))

	def test_04_payment_mode_validation_is_enforced_server_side(self):
		for paid_amount in ("0", "-1", "100", "101"):
			with self.subTest(paid_amount=paid_amount), self.assertRaises(
				frappe.ValidationError
			):
				self.make_purchase(
					payment_status="PARTIAL",
					paid_amount=paid_amount,
					payment_method="CASH",
				).insert()

		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(payment_status="PARTIAL", paid_amount="40").insert()
		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(payment_status="PAID").insert()

		credit = self.submit_purchase(
			payment_status="CREDIT", paid_amount="55", payment_method="CASH"
		)
		paid = self.submit_purchase(
			payment_status="PAID", paid_amount="55", payment_method="CASH"
		)
		self.assertEqual(Decimal(str(credit.paid_amount)), Decimal("0.000"))
		self.assertFalse(self.automatic_payments(credit))
		self.assertEqual(Decimal(str(paid.paid_amount)), Decimal("100.000"))
		self.assertEqual(Decimal(str(self.automatic_payments(paid)[0].amount)), Decimal("100.000"))

	def test_05_resubmit_repost_and_reload_paths_are_idempotent(self):
		purchase = self.submit_purchase(payment_status="PAID", payment_method="CASH")
		balance_before = self.supplier_balance()
		stock_before = self.stock_balance()

		purchase.submit()
		reloaded = frappe.get_doc("Purchase", purchase.name)
		reloaded.create_automatic_supplier_payment()
		with self.assertRaises(frappe.ValidationError):
			reloaded.create_supplier_transaction()
		with self.assertRaises(frappe.ValidationError):
			reloaded.create_stock_movements()

		self.assertEqual(len(self.automatic_payments(purchase)), 1)
		self.assertEqual(
			frappe.db.count(
				"Supplier Transaction", {"source_purchase": purchase.name, "docstatus": 1}
			),
			1,
		)
		self.assertEqual(
			frappe.db.count(
				"Stock Movement", {"source_purchase": purchase.name, "docstatus": 1}
			),
			1,
		)
		self.assertEqual(self.supplier_balance(), balance_before)
		self.assertEqual(self.stock_balance(), stock_before)

	def test_06_credit_purchase_reversal_restores_stock_and_debt(self):
		purchase = self.submit_purchase(payment_status="CREDIT")
		original_transaction = self.purchase_transaction(purchase)
		original_movement = frappe.db.get_value(
			"Stock Movement",
			{"source_purchase": purchase.name, "docstatus": 1},
			["name", "docstatus"],
			as_dict=True,
		)
		reversal = self.submit_reversal(purchase)
		reversal_entry = frappe.db.get_value(
			"Supplier Transaction",
			{"source_purchase_reversal": reversal.name, "docstatus": 1},
			["direction", "amount", "reversal_of"],
			as_dict=True,
		)

		self.assertEqual(self.stock_balance(), Decimal("10"))
		self.assertEqual(self.supplier_balance(), Decimal("0.000"))
		self.assertEqual(reversal_entry.direction, "CREDIT")
		self.assertEqual(Decimal(str(reversal_entry.amount)), Decimal("100.000"))
		self.assertEqual(reversal_entry.reversal_of, original_transaction.name)
		self.assertEqual(frappe.db.get_value("Purchase", purchase.name, "docstatus"), 1)
		self.assertEqual(
			frappe.db.get_value("Stock Movement", original_movement.name, "docstatus"), 1
		)

	def test_07_paid_purchase_reversal_reverses_payment_and_preserves_audit(self):
		purchase = self.submit_purchase(payment_status="PAID", payment_method="CASH")
		payment = self.automatic_payments(purchase)[0]
		reversal = self.submit_reversal(purchase)
		payment_reversals = frappe.get_all(
			"Supplier Payment Reversal",
			filters={"original_supplier_payment": payment.name, "docstatus": 1},
			fields=["name", "original_supplier_payment"],
		)

		self.assertEqual(self.stock_balance(), Decimal("10"))
		self.assertEqual(self.supplier_balance(), Decimal("0.000"))
		self.assertEqual(len(payment_reversals), 1)
		self.assertEqual(
			reversal.automatic_supplier_payment_reversal, payment_reversals[0].name
		)
		self.assertEqual(frappe.db.get_value("Purchase", purchase.name, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Supplier Payment", payment.name, "docstatus"), 1)
		self.assertEqual(
			frappe.db.get_value("Supplier Payment", payment.name, "source_purchase"),
			purchase.name,
		)

	def test_08_partial_purchase_reversal_returns_to_exact_prior_balance(self):
		opening_purchase = self.submit_purchase(
			payment_status="CREDIT", business_date="2026-08-20"
		)
		balance_before = self.supplier_balance()
		stock_before = self.stock_balance()
		partial = self.submit_purchase(
			payment_status="PARTIAL",
			paid_amount="40",
			payment_method="CASH",
			business_date="2026-08-21",
		)
		payment = self.automatic_payments(partial)[0]
		reversal = self.submit_reversal(partial)

		self.assertEqual(self.supplier_balance(), balance_before)
		self.assertEqual(self.stock_balance(), stock_before)
		self.assertEqual(
			frappe.db.count(
				"Supplier Payment Reversal",
				{"original_supplier_payment": payment.name, "docstatus": 1},
			),
			1,
		)
		self.assertEqual(
			frappe.db.count(
				"Supplier Transaction",
				{"source_purchase_reversal": reversal.name, "docstatus": 1},
			),
			1,
		)
		self.assertEqual(self.purchase_transaction(opening_purchase).direction, "DEBIT")

	def test_09_second_reversal_is_rejected_without_duplicate_effects(self):
		purchase = self.submit_purchase(payment_status="PAID", payment_method="CASH")
		payment = self.automatic_payments(purchase)[0]
		first = self.submit_reversal(purchase)
		balance_before = self.supplier_balance()
		stock_before = self.stock_balance()

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Purchase Reversal",
					"business": self.business.name,
					"original_purchase": purchase.name,
					"business_date": "2026-08-24",
					"reason": "Duplicate reversal attempt",
				}
			).insert()

		self.assertEqual(
			frappe.db.count(
				"Supplier Transaction",
				{"source_purchase_reversal": first.name, "docstatus": 1},
			),
			1,
		)
		self.assertEqual(
			frappe.db.count(
				"Supplier Payment Reversal",
				{"original_supplier_payment": payment.name, "docstatus": 1},
			),
			1,
		)
		self.assertEqual(
			frappe.db.count(
				"Stock Movement", {"source_purchase_reversal": first.name, "docstatus": 1}
			),
			1,
		)
		self.assertEqual(self.supplier_balance(), balance_before)
		self.assertEqual(self.stock_balance(), stock_before)

	def test_10_cross_business_supplier_and_product_are_rejected(self):
		other_business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Foreign {uuid4().hex[:10]}"}
		).insert()
		other_supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": other_business.name,
				"supplier_name": f"Foreign Supplier {uuid4().hex[:10]}",
			}
		).insert()
		other_product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": other_business.name,
				"product_name": f"Foreign Product {uuid4().hex[:10]}",
				"reference": f"FOREIGN-{uuid4().hex[:10]}",
				"primary_unit": "UNIT",
			}
		).insert()

		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(supplier=other_supplier.name).insert()
		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(product=other_product.name).insert()

		self.assertEqual(self.stock_balance(), Decimal("10"))
		self.assertEqual(self.supplier_balance(), Decimal("0.000"))
		self.assertEqual(frappe.db.count("Supplier Payment", {"supplier": self.supplier.name}), 0)
		self.assertEqual(
			frappe.db.count("Supplier Transaction", {"supplier": self.supplier.name}), 0
		)

	def test_11_supplier_statement_has_one_correct_total_and_closing_balance(self):
		credit = self.submit_purchase(
			payment_status="CREDIT", business_date="2026-08-20"
		)
		paid = self.submit_purchase(
			payment_status="PAID",
			payment_method="CASH",
			business_date="2026-08-21",
		)
		partial = self.submit_purchase(
			payment_status="PARTIAL",
			paid_amount="40",
			payment_method="CASH",
			business_date="2026-08-22",
		)
		self.submit_reversal(credit, business_date="2026-08-23")
		rows, skip_total_row = self.statement()
		ledger_rows = rows[:-1]
		total_rows = [row for row in rows if row.transaction_type == "Total"]

		self.assertTrue(skip_total_row)
		self.assertEqual(len(total_rows), 1)
		self.assertEqual(len(ledger_rows), 6)
		self.assertEqual(rows[-1].debit, Decimal("300.000"))
		self.assertEqual(rows[-1].credit, Decimal("240.000"))
		self.assertEqual(rows[-1].running_balance, Decimal("60.000"))
		self.assertEqual(self.supplier_balance(), Decimal("60.000"))
		self.assertEqual(
			{row.reference for row in ledger_rows},
			{
				credit.name,
				paid.name,
				paid.automatic_supplier_payment,
				partial.name,
				partial.automatic_supplier_payment,
				frappe.db.get_value(
					"Purchase Reversal", {"original_purchase": credit.name}, "name"
				),
			},
		)

	def test_12_supplier_balances_matches_unreversed_ledger_and_controller(self):
		self.submit_purchase(payment_status="CREDIT", business_date="2026-08-20")
		self.submit_purchase(
			payment_status="PAID",
			payment_method="CASH",
			business_date="2026-08-21",
		)
		self.submit_purchase(
			payment_status="PARTIAL",
			paid_amount="40",
			payment_method="CASH",
			business_date="2026-08-22",
		)
		row = self.supplier_balance_row()
		rows, _ = self.statement()

		self.assertEqual(Decimal(self.supplier.get_total_purchases()), Decimal("300.000"))
		self.assertEqual(Decimal(self.supplier.get_total_payments()), Decimal("140.000"))
		self.assertEqual(row.outstanding_balance, Decimal("160.000"))
		self.assertEqual(rows[-1].running_balance, Decimal("160.000"))
		self.assertEqual(self.supplier_balance(), Decimal("160.000"))
		self.assertEqual(row.last_purchase_date, frappe.utils.getdate("2026-08-22"))
		self.assertEqual(row.last_payment_date, frappe.utils.getdate("2026-08-22"))
		self.assertEqual(self.supplier.get_last_purchase(), frappe.utils.getdate("2026-08-22"))
		self.assertEqual(self.supplier.get_last_payment(), frappe.utils.getdate("2026-08-22"))

	def test_13_purchases_report_filters_totals_and_reversal_treatment(self):
		credit = self.submit_purchase(
			payment_status="CREDIT", business_date="2026-08-20"
		)
		paid = self.submit_purchase(
			payment_status="PAID",
			payment_method="CASH",
			business_date="2026-08-21",
		)
		partial = self.submit_purchase(
			payment_status="PARTIAL",
			paid_amount="40",
			payment_method="CASH",
			business_date="2026-08-22",
		)
		self.submit_reversal(credit, business_date="2026-08-23")

		_, rows = purchases_report(
			{
				"business": self.business.name,
				"supplier": self.supplier.name,
				"from_date": "2026-08-20",
				"to_date": "2026-08-22",
			}
		)
		by_purchase = {row.purchase: row for row in rows}
		self.assertEqual(set(by_purchase), {credit.name, paid.name, partial.name})
		self.assertEqual(by_purchase[credit.name].reversal_status, "Reversed")
		self.assertEqual(by_purchase[credit.name].supplier_debt_effect, Decimal("0.000"))
		self.assertEqual(by_purchase[paid.name].supplier_debt_effect, Decimal("0.000"))
		self.assertEqual(by_purchase[partial.name].supplier_debt_effect, Decimal("60.000"))
		self.assertEqual(sum(row.total_amount for row in rows), Decimal("300.000"))
		self.assertEqual(sum(row.supplier_debt_effect for row in rows), Decimal("60.000"))

		_, outside_range = purchases_report(
			{
				"business": self.business.name,
				"supplier": self.supplier.name,
				"from_date": "2026-08-24",
				"to_date": "2026-08-25",
			}
		)
		self.assertFalse(outside_range)
		_, wrong_supplier = purchases_report(
			{
				"business": self.business.name,
				"supplier": "NONEXISTENT-SUPPLIER",
				"from_date": "2026-08-20",
				"to_date": "2026-08-22",
			}
		)
		self.assertFalse(wrong_supplier)

	def test_14_stock_reconciles_for_all_payment_modes_and_reversals(self):
		credit = self.submit_purchase(payment_status="CREDIT")
		self.assertEqual(self.stock_balance(), Decimal("12"))
		paid = self.submit_purchase(payment_status="PAID", payment_method="CASH")
		self.assertEqual(self.stock_balance(), Decimal("14"))
		partial = self.submit_purchase(
			payment_status="PARTIAL", paid_amount="40", payment_method="CASH"
		)
		self.assertEqual(self.stock_balance(), Decimal("16"))

		self.submit_reversal(credit)
		self.assertEqual(self.stock_balance(), Decimal("14"))
		self.submit_reversal(paid)
		self.assertEqual(self.stock_balance(), Decimal("12"))
		self.submit_reversal(partial)
		self.assertEqual(self.stock_balance(), Decimal("10"))

		_, rows = stock_on_hand(
			{
				"business": self.business.name,
				"product": self.product.name,
				"show_zero_stock": 1,
			}
		)
		self.assertEqual(rows[0].stock_on_hand, Decimal("10"))
		movements = frappe.get_all(
			"Stock Movement",
			filters={"business": self.business.name, "product": self.product.name, "docstatus": 1},
			fields=["direction", "quantity"],
		)
		reconciled = sum(
			Decimal(str(row.quantity)) if row.direction == "IN" else -Decimal(str(row.quantity))
			for row in movements
		)
		self.assertEqual(reconciled, rows[0].stock_on_hand)

	def test_15_purchase_ledger_statement_balances_and_stock_fully_reconcile(self):
		purchases = [
			self.submit_purchase(payment_status="CREDIT", business_date="2026-08-20"),
			self.submit_purchase(
				payment_status="PAID",
				payment_method="CASH",
				business_date="2026-08-21",
			),
			self.submit_purchase(
				payment_status="PARTIAL",
				paid_amount="40",
				payment_method="CASH",
				business_date="2026-08-22",
			),
		]
		for purchase in purchases:
			self.submit_reversal(purchase, business_date="2026-08-24")

		ledger_balance = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(CASE WHEN direction='DEBIT' THEN amount ELSE -amount END), 0)
			FROM `tabSupplier Transaction`
			WHERE business=%s AND supplier=%s AND docstatus=1
			""",
			(self.business.name, self.supplier.name),
		)[0][0]
		statement_rows, _ = self.statement()
		balance_row = self.supplier_balance_row()
		_, stock_rows = stock_on_hand(
			{
				"business": self.business.name,
				"product": self.product.name,
				"show_zero_stock": 1,
			}
		)
		purchase_quantity = sum(
			Decimal(str(item.base_quantity)) for purchase in purchases for item in purchase.items
		)
		purchase_movement_quantity = sum(
			Decimal(str(value))
			for value in frappe.get_all(
				"Stock Movement",
				filters={"source_purchase": ["in", [purchase.name for purchase in purchases]], "docstatus": 1},
				pluck="quantity",
			)
		)
		reversal_movement_quantity = sum(
			Decimal(str(value))
			for value in frappe.get_all(
				"Stock Movement",
				filters={"source_purchase_reversal": ["is", "set"], "business": self.business.name, "docstatus": 1},
				pluck="quantity",
			)
		)

		self.assertEqual(Decimal(str(ledger_balance)), Decimal("0.000"))
		self.assertEqual(statement_rows[-1].running_balance, Decimal("0.000"))
		self.assertEqual(balance_row.outstanding_balance, Decimal("0.000"))
		self.assertEqual(self.supplier_balance(), Decimal("0.000"))
		self.assertEqual(purchase_quantity, Decimal("6"))
		self.assertEqual(purchase_movement_quantity, purchase_quantity)
		self.assertEqual(reversal_movement_quantity, purchase_quantity)
		self.assertEqual(stock_rows[0].stock_on_hand, Decimal("10"))
		self.assertEqual(self.stock_balance(), Decimal("10"))
