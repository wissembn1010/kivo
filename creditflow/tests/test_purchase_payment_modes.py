from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow import dashboard
from creditflow.creditflow.report.supplier_balances.supplier_balances import (
	execute as supplier_balances,
)
from creditflow.creditflow.report.purchases_report.purchases_report import (
	execute as purchases_report,
)
from creditflow.creditflow.report.supplier_statement.supplier_statement import (
	execute as supplier_statement,
)


class IntegrationTestPurchasePaymentModes(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Purchase Payment Business {suffix}"}
		).insert()
		self.supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": self.business.name,
				"supplier_name": f"Purchase Payment Supplier {suffix}",
			}
		).insert()
		self.product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business.name,
				"product_name": f"Purchase Payment Product {suffix}",
				"reference": f"PUR-PAY-{suffix}",
				"primary_unit": "UNIT",
			}
		).insert()

	def make_purchase(self, status=None, paid_amount=None, payment_method=None):
		values = {
			"doctype": "Purchase",
			"business": self.business.name,
			"supplier": self.supplier.name,
			"business_date": frappe.utils.today(),
			"items": [{"product": self.product.name, "quantity": "1", "unit_cost": "80"}],
		}
		if status is not None:
			values["payment_status"] = status
		if paid_amount is not None:
			values["paid_amount"] = paid_amount
		if payment_method is not None:
			values["payment_method"] = payment_method
		return frappe.get_doc(values)

	def submit_purchase(self, **kwargs):
		purchase = self.make_purchase(**kwargs).insert()
		purchase.submit()
		return purchase

	def supplier_balance(self):
		return Decimal(self.supplier.get_ledger_balance())

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

	def automatic_payments(self, purchase):
		return frappe.get_all(
			"Supplier Payment",
			filters={"source_purchase": purchase.name, "docstatus": 1},
			fields=["name", "amount", "payment_method", "business_date"],
		)

	def test_credit_purchase_posts_stock_and_full_supplier_debt_without_payment(self):
		purchase = self.submit_purchase(status="CREDIT")

		self.assertEqual(self.stock_balance(), Decimal("1"))
		self.assertEqual(self.supplier_balance(), Decimal("80.000"))
		self.assertEqual(purchase.paid_amount, Decimal("0.000"))
		self.assertFalse(self.automatic_payments(purchase))

	def test_existing_purchase_without_payment_status_remains_credit(self):
		purchase = self.submit_purchase()

		self.assertEqual(purchase.payment_status, "CREDIT")
		self.assertEqual(self.supplier_balance(), Decimal("80.000"))
		self.assertFalse(self.automatic_payments(purchase))

	def test_paid_purchase_posts_purchase_and_payment_with_zero_final_debt(self):
		purchase = self.submit_purchase(
			status="PAID", paid_amount="10", payment_method="CASH"
		)
		payments = self.automatic_payments(purchase)

		self.assertEqual(self.stock_balance(), Decimal("1"))
		self.assertEqual(self.supplier_balance(), Decimal("0.000"))
		self.assertEqual(purchase.paid_amount, Decimal("80.000"))
		self.assertEqual(len(payments), 1)
		self.assertEqual(Decimal(str(payments[0].amount)), Decimal("80.000"))
		self.assertEqual(purchase.automatic_supplier_payment, payments[0].name)
		self.assertEqual(
			frappe.db.count("Supplier Transaction", {"source_purchase": purchase.name, "docstatus": 1}),
			1,
		)
		self.assertEqual(
			frappe.db.count(
				"Supplier Transaction",
				{"source_supplier_payment": payments[0].name, "docstatus": 1},
			),
			1,
		)

	def test_partial_purchase_posts_only_remaining_supplier_debt(self):
		purchase = self.submit_purchase(
			status="PARTIAL", paid_amount="30", payment_method="CASH"
		)
		payments = self.automatic_payments(purchase)

		self.assertEqual(self.stock_balance(), Decimal("1"))
		self.assertEqual(self.supplier_balance(), Decimal("50.000"))
		self.assertEqual(len(payments), 1)
		self.assertEqual(Decimal(str(payments[0].amount)), Decimal("30.000"))

	def test_invalid_partial_amounts_are_rejected(self):
		for amount in ("0", "80", "81", "-1"):
			with self.subTest(amount=amount), self.assertRaises(frappe.ValidationError):
				self.make_purchase(
					status="PARTIAL", paid_amount=amount, payment_method="CASH"
				).insert()

	def test_paid_purchase_requires_payment_method(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(status="PAID").insert()

	def test_second_submit_does_not_duplicate_automatic_payment(self):
		purchase = self.submit_purchase(status="PAID", payment_method="CASH")

		purchase.submit()

		self.assertEqual(len(self.automatic_payments(purchase)), 1)

	def test_rejected_generated_payment_rolls_back_purchase_effects(self):
		# Historical supplier credit makes a new full cash settlement an overpayment.
		opening = frappe.get_doc({"doctype": "Supplier Transaction", "business": self.business.name,
			"supplier": self.supplier.name, "transaction_type": "OPENING_BALANCE",
			"direction": "CREDIT", "amount": "10.000",
			"transaction_date": frappe.utils.today()}).insert()
		opening.submit()
		purchase = self.make_purchase(status="PAID", payment_method="CASH").insert()
		with self.assertRaisesRegex(frappe.ValidationError, "outstanding debt"):
			purchase.submit()
		self.assertEqual(frappe.db.get_value("Purchase", purchase.name, "docstatus"), 0)
		self.assertEqual(frappe.db.count("Stock Movement", {"source_purchase": purchase.name}), 0)
		self.assertEqual(frappe.db.count("Supplier Transaction", {"source_purchase": purchase.name}), 0)
		self.assertEqual(frappe.db.count("Supplier Payment", {"source_purchase": purchase.name}), 0)
		self.assertEqual(self.supplier_balance(), Decimal("-10.000"))

	def test_purchase_reversal_reverses_automatic_payment_stock_and_debt(self):
		purchase = self.submit_purchase(
			status="PARTIAL", paid_amount="30", payment_method="CASH"
		)
		payment = self.automatic_payments(purchase)[0]
		reversal = frappe.get_doc(
			{
				"doctype": "Purchase Reversal",
				"business": self.business.name,
				"original_purchase": purchase.name,
				"business_date": frappe.utils.today(),
				"reason": "Reverse payment-mode test purchase",
			}
		).insert()
		reversal.submit()

		self.assertEqual(self.stock_balance(), Decimal("0"))
		self.assertEqual(self.supplier_balance(), Decimal("0.000"))
		self.assertEqual(
			frappe.db.count(
				"Supplier Payment Reversal",
				{"original_supplier_payment": payment.name, "docstatus": 1},
			),
			1,
		)
		self.assertTrue(reversal.automatic_supplier_payment_reversal)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Purchase Reversal",
					"business": self.business.name,
					"original_purchase": purchase.name,
					"business_date": frappe.utils.today(),
					"reason": "Duplicate reversal",
				}
			).insert()

	def test_reversals_do_not_imply_a_supplier_cash_refund(self):
		baseline = dashboard.supplier_payments_today()["value"]
		credit = self.submit_purchase(status="CREDIT")
		credit_reversal = frappe.get_doc({"doctype": "Purchase Reversal", "business": self.business.name,
			"original_purchase": credit.name, "business_date": frappe.utils.today(), "reason": "Credit reversal"}).insert()
		credit_reversal.submit()
		self.assertEqual(dashboard.supplier_payments_today()["value"], baseline)

		paid = self.submit_purchase(status="PAID", payment_method="CASH")
		paid_reversal = frappe.get_doc({"doctype": "Purchase Reversal", "business": self.business.name,
			"original_purchase": paid.name, "business_date": frappe.utils.today(), "reason": "Paid reversal"}).insert()
		paid_reversal.submit()
		self.assertTrue(paid_reversal.automatic_supplier_payment_reversal)
		self.assertEqual(dashboard.supplier_payments_today()["value"], baseline + Decimal("80.000"))

		partial = self.submit_purchase(status="PARTIAL", paid_amount="30", payment_method="CASH")
		partial_reversal = frappe.get_doc({"doctype": "Purchase Reversal", "business": self.business.name,
			"original_purchase": partial.name, "business_date": frappe.utils.today(), "reason": "Partial reversal"}).insert()
		partial_reversal.submit()
		self.assertEqual(self.supplier_balance(), Decimal("0.000"))
		self.assertEqual(dashboard.supplier_payments_today()["value"], baseline + Decimal("110.000"))

	def test_historical_payment_reversal_is_not_a_today_cash_recovery(self):
		baseline = dashboard.supplier_payments_today()["value"]
		purchase = self.make_purchase(status="PAID", payment_method="CASH")
		purchase.business_date = frappe.utils.add_days(frappe.utils.today(), -1)
		purchase.insert().submit()
		reversal = frappe.get_doc({"doctype": "Purchase Reversal", "business": self.business.name,
			"original_purchase": purchase.name, "business_date": frappe.utils.today(),
			"reason": "Historical paid purchase correction"}).insert()
		reversal.submit()
		self.assertEqual(dashboard.supplier_payments_today()["value"], baseline)

	def test_standalone_payment_reversal_does_not_reduce_cash_paid_metric(self):
		baseline = dashboard.supplier_payments_today()["value"]
		self.submit_purchase(status="CREDIT")
		payment = frappe.get_doc({"doctype": "Supplier Payment", "business": self.business.name,
			"supplier": self.supplier.name, "business_date": frappe.utils.today(),
			"amount": "30.000", "payment_method": "CASH"}).insert()
		payment.submit()
		reversal = frappe.get_doc({"doctype": "Supplier Payment Reversal", "business": self.business.name,
			"original_supplier_payment": payment.name, "business_date": frappe.utils.today(),
			"reason": "Ledger correction, no refund"}).insert()
		reversal.submit()
		self.assertEqual(self.supplier_balance(), Decimal("80.000"))
		self.assertEqual(dashboard.supplier_payments_today()["value"], baseline + Decimal("30.000"))

	def test_supplier_statement_shows_purchase_payment_and_closing_balance(self):
		purchase = self.submit_purchase(
			status="PARTIAL", paid_amount="30", payment_method="CASH"
		)
		payment = self.automatic_payments(purchase)[0]

		_, rows, *_ = supplier_statement({"supplier": self.supplier.name})
		ledger_rows = rows[:-1]
		purchase_row = next(row for row in ledger_rows if row.reference == purchase.name)
		payment_row = next(row for row in ledger_rows if row.reference == payment.name)

		self.assertEqual(purchase_row.debit, Decimal("80.000"))
		self.assertEqual(payment_row.credit, Decimal("30.000"))
		self.assertEqual(rows[-1].running_balance, Decimal("50.000"))

	def test_supplier_balances_and_last_payment_include_automatic_payment(self):
		purchase = self.submit_purchase(
			status="PARTIAL", paid_amount="30", payment_method="CASH"
		)

		_, rows = supplier_balances(
			{"business": self.business.name, "supplier": self.supplier.name}
		)
		self.assertEqual(rows[0].outstanding_balance, Decimal("50.000"))
		self.assertEqual(rows[0].last_payment_date, frappe.utils.getdate(purchase.business_date))
		self.assertEqual(
			self.supplier.get_last_payment(), frappe.utils.getdate(purchase.business_date)
		)

	def test_purchases_report_shows_only_remaining_debt(self):
		paid = self.submit_purchase(status="PAID", payment_method="CASH")
		partial = self.submit_purchase(
			status="PARTIAL", paid_amount="30", payment_method="CASH"
		)

		_, rows = purchases_report({"business": self.business.name})
		by_purchase = {row.purchase: row for row in rows}
		self.assertEqual(by_purchase[paid.name].supplier_debt_effect, Decimal("0.000"))
		self.assertEqual(by_purchase[partial.name].supplier_debt_effect, Decimal("50.000"))
