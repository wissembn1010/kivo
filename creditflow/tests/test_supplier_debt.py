from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow import dashboard


class IntegrationTestSupplierDebt(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:10]
		self.business = self.make_business(f"Supplier Debt Business {suffix}")
		self.other_business = self.make_business(f"Other Supplier Debt Business {suffix}")
		self.supplier = self.make_supplier(self.business.name, f"Supplier Debt Supplier {suffix}")
		self.other_supplier = self.make_supplier(
			self.other_business.name, f"Other Supplier Debt Supplier {suffix}"
		)
		self.product = self.make_product(self.business.name, f"SUPPLIER-DEBT-{suffix}")

	def make_business(self, business_name):
		return frappe.get_doc({"doctype": "Business", "business_name": business_name}).insert()

	def make_supplier(self, business, supplier_name):
		return frappe.get_doc(
			{"doctype": "Supplier", "business": business, "supplier_name": supplier_name}
		).insert()

	def make_product(self, business, reference):
		return frappe.get_doc(
			{
				"doctype": "Product",
				"business": business,
				"product_name": reference,
				"reference": reference,
			}
		).insert()

	def make_purchase(self, supplier=None, product=None, quantity="2", unit_cost="3.4567"):
		purchase = frappe.get_doc(
			{
				"doctype": "Purchase",
				"business": self.business.name,
				"supplier": supplier or self.supplier.name,
				"business_date": frappe.utils.today(),
			}
		)
		purchase.append(
			"items",
			{"product": product or self.product.name, "quantity": quantity, "unit_cost": unit_cost},
		)
		return purchase

	def make_supplier_payment(self, amount="25.125", supplier=None, payment_method="CASH"):
		return frappe.get_doc(
			{
				"doctype": "Supplier Payment",
				"business": self.business.name,
				"supplier": supplier or self.supplier.name,
				"business_date": frappe.utils.today(),
				"amount": amount,
				"payment_method": payment_method,
			}
		)

	def make_reversal(self, purchase):
		return frappe.get_doc(
			{
				"doctype": "Purchase Reversal",
				"original_purchase": purchase.name,
				"business": self.business.name,
				"business_date": frappe.utils.today(),
				"reason": "Supplier debt reversal test",
			}
		)

	def add_opening_balance(self, amount="100.000"):
		transaction = frappe.get_doc(
			{
				"doctype": "Supplier Transaction",
				"business": self.business.name,
				"supplier": self.supplier.name,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": amount,
				"transaction_date": frappe.utils.today(),
			}
		).insert()
		transaction.submit()
		return transaction

	def test_purchase_increases_supplier_debt(self):
		purchase = self.make_purchase()
		purchase.insert()
		purchase.submit()
		self.assertEqual(Decimal(self.supplier.get_ledger_balance()), Decimal("6.913"))
		self.assertEqual(Decimal(str(purchase.total_amount)), Decimal("6.913"))

	def test_supplier_payment_decreases_debt(self):
		self.add_opening_balance("100.000")
		payment = self.make_supplier_payment("25.125")
		payment.insert()
		payment.submit()
		self.assertEqual(Decimal(self.supplier.get_ledger_balance()), Decimal("74.875"))

	def test_exact_supplier_settlement_succeeds(self):
		self.add_opening_balance("100.000")
		payment = self.make_supplier_payment("100.000").insert()
		payment.submit()
		self.assertEqual(Decimal(self.supplier.get_ledger_balance()), Decimal("0.000"))

	def test_supplier_overpayment_is_rejected_without_ledger_effect(self):
		self.add_opening_balance("100.000")
		with self.assertRaisesRegex(frappe.ValidationError, "outstanding debt"):
			self.make_supplier_payment("100.001").insert()
		self.assertEqual(Decimal(self.supplier.get_ledger_balance()), Decimal("100.000"))
		self.assertEqual(frappe.db.count("Supplier Transaction", {"supplier": self.supplier.name, "transaction_type": "SUPPLIER_PAYMENT"}), 0)

	def test_supplier_payment_cannot_borrow_another_tenants_debt(self):
		self.add_opening_balance("100.000")
		with self.assertRaisesRegex(frappe.ValidationError, "same Business"):
			self.make_supplier_payment("1.000", supplier=self.other_supplier.name).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "outstanding debt"):
			frappe.get_doc({"doctype": "Supplier Payment", "business": self.other_business.name,
				"supplier": self.other_supplier.name, "business_date": frappe.utils.today(),
				"amount": "1.000", "payment_method": "CASH"}).insert()

	def test_purchase_reversal_decreases_debt(self):
		purchase = self.make_purchase(quantity="10", unit_cost="2")
		purchase.insert()
		purchase.submit()
		reversal = self.make_reversal(purchase)
		reversal.insert()
		reversal.submit()
		self.assertEqual(Decimal(self.supplier.get_ledger_balance()), Decimal("0.000"))

	def test_payment_reversal_restores_debt(self):
		self.add_opening_balance("100.000")
		payment = self.make_supplier_payment("25.125")
		payment.insert()
		payment.submit()
		reversal = frappe.get_doc(
			{
				"doctype": "Supplier Payment Reversal",
				"business": self.business.name,
				"original_supplier_payment": payment.name,
				"business_date": frappe.utils.today(),
				"reason": "Restore debt",
			}
		)
		reversal.insert()
		reversal.submit()
		self.assertEqual(Decimal(self.supplier.get_ledger_balance()), Decimal("100.000"))

	def test_zero_and_negative_supplier_payments_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_supplier_payment("0").validate()
		with self.assertRaises(frappe.ValidationError):
			self.make_supplier_payment("-1").validate()

	def test_wrong_business_supplier_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(supplier=self.other_supplier.name).validate()

	def test_fake_purchase_ledger_transaction_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Supplier Transaction",
					"business": self.business.name,
					"supplier": self.supplier.name,
					"transaction_type": "PURCHASE",
					"direction": "DEBIT",
					"amount": "100",
					"transaction_date": frappe.utils.today(),
					"source_purchase": "NONEXISTENT-PURCHASE",
				}
			).validate()

	def test_duplicate_source_posting_rejected(self):
		purchase = self.make_purchase(quantity="10", unit_cost="2")
		purchase.insert()
		purchase.submit()
		duplicate = frappe.get_doc(
			{
				"doctype": "Supplier Transaction",
				"business": self.business.name,
				"supplier": self.supplier.name,
				"transaction_type": "PURCHASE",
				"direction": "DEBIT",
				"amount": purchase.total_amount,
				"transaction_date": frappe.utils.today(),
				"source_purchase": purchase.name,
			}
		)
		duplicate.flags.creditflow_system_generated = True
		with self.assertRaises(frappe.ValidationError):
			duplicate.insert()

	def test_supplier_balance_calculation_correct(self):
		self.add_opening_balance("150.000")
		purchase = self.make_purchase(quantity="5", unit_cost="10")
		purchase.insert()
		purchase.submit()
		payment = self.make_supplier_payment("60")
		payment.insert()
		payment.submit()
		purchase_reversal = self.make_reversal(purchase)
		purchase_reversal.insert()
		purchase_reversal.submit()
		self.assertEqual(Decimal(self.supplier.get_ledger_balance()), Decimal("90.000"))

	def test_dashboard_total_supplier_debt_correct(self):
		# Capture baseline to avoid interference from pre-existing development-site data
		baseline = dashboard.total_supplier_debt()["value"]
		payments_baseline = dashboard.supplier_payments_today()["value"]
		self.add_opening_balance("150.000")
		purchase = self.make_purchase(quantity="5", unit_cost="10")
		purchase.insert()
		purchase.submit()
		payment = self.make_supplier_payment("60")
		payment.insert()
		payment.submit()
		# Expected delta: opening 150 + purchase 50 - payment 60 = +140
		expected_delta = Decimal("140.000")
		self.assertEqual(dashboard.total_supplier_debt()["value"], baseline + expected_delta)
		self.assertEqual(
			dashboard.supplier_payments_today()["value"], payments_baseline + Decimal("60.000")
		)
