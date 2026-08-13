from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestPayment(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Payment Test Business {suffix}"}
		).insert()
		self.other_business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Other Payment Test Business {suffix}"}
		).insert()
		self.customer = self.make_customer(
			self.business.name, f"Payment Test Customer {suffix}"
		)
		self.other_customer = self.make_customer(
			self.other_business.name, f"Other Payment Test Customer {suffix}"
		)

	def make_customer(self, business, customer_name):
		return frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer_name,
				"business": business,
			}
		).insert()

	def make_payment(self, amount="25.125", customer=None):
		return frappe.get_doc(
			{
				"doctype": "Payment",
				"business": self.business.name,
				"customer": customer or self.customer.name,
				"amount": amount,
				"business_date": frappe.utils.today(),
			}
		)

	def add_opening_balance(self, amount):
		transaction = frappe.get_doc(
			{
				"doctype": "Credit Transaction",
				"business": self.business.name,
				"customer": self.customer.name,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": amount,
				"transaction_date": frappe.utils.today(),
			}
		).insert()
		transaction.submit()

	def test_successful_payment_posts_one_credit_transaction(self):
		self.add_opening_balance("100.000")
		payment = self.make_payment("25.125")
		payment.insert()
		payment.submit()

		transactions = frappe.get_all(
			"Credit Transaction",
			filters={"source_payment": payment.name},
			fields=[
				"docstatus",
				"transaction_type",
				"direction",
				"amount",
				"source_payment",
			],
		)
		self.assertEqual(payment.docstatus, 1)
		self.assertEqual(len(transactions), 1)
		self.assertEqual(transactions[0].docstatus, 1)
		self.assertEqual(transactions[0].transaction_type, "PAYMENT")
		self.assertEqual(transactions[0].direction, "CREDIT")
		self.assertEqual(Decimal(str(transactions[0].amount)), Decimal("25.125"))
		self.assertEqual(transactions[0].source_payment, payment.name)
		self.assertEqual(
			Decimal(self.customer.get_ledger_balance()), Decimal("74.875")
		)

	def test_zero_amount_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_payment("0").validate()

	def test_negative_amount_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_payment("-1").validate()

	def test_customer_from_another_business_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_payment(customer=self.other_customer.name).validate()

	def test_credit_transaction_failure_has_no_partial_side_effects(self):
		payment = self.make_payment()
		payment.insert()

		with patch(
			"creditflow.creditflow.doctype.credit_transaction.credit_transaction.CreditTransaction.submit",
			side_effect=frappe.ValidationError("Forced transaction failure"),
		):
			with self.assertRaises(frappe.ValidationError):
				payment.submit()

		self.assertEqual(
			frappe.db.count("Credit Transaction", {"source_payment": payment.name}), 0
		)
		self.assertEqual(frappe.db.get_value("Payment", payment.name, "docstatus"), 0)

	def test_posted_payment_cannot_be_cancelled(self):
		payment = self.make_payment()
		payment.insert()
		payment.submit()

		with self.assertRaises(frappe.ValidationError):
			payment.cancel()
