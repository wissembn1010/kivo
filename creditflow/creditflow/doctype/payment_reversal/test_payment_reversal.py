from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestPaymentReversal(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:10]
		self.business = self.make_business(f"Payment Reversal Business {suffix}")
		self.other_business = self.make_business(
			f"Other Payment Reversal Business {suffix}"
		)
		self.customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"Payment Reversal Customer {suffix}",
				"business": self.business.name,
			}
		).insert()
		self.add_opening_balance()

	def make_business(self, business_name):
		return frappe.get_doc(
			{"doctype": "Business", "business_name": business_name}
		).insert()

	def add_opening_balance(self, amount="100.000"):
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

	def make_payment(self, submit=True):
		payment = frappe.get_doc(
			{
				"doctype": "Payment",
				"business": self.business.name,
				"customer": self.customer.name,
				"amount": "25.125",
				"business_date": frappe.utils.today(),
			}
		).insert()
		if submit:
			payment.submit()
		return payment

	def make_reversal(self, payment, business=None):
		return frappe.get_doc(
			{
				"doctype": "Payment Reversal",
				"original_payment": payment.name,
				"business": business or payment.business,
				"business_date": frappe.utils.today(),
				"reason": "Test payment reversal",
			}
		)

	def test_successful_reversal_is_append_only(self):
		self.add_opening_balance()
		balance_before_payment = Decimal(self.customer.get_ledger_balance())
		payment = self.make_payment()
		original_payment = frappe.get_doc("Payment", payment.name).as_dict()
		original_transaction = frappe.get_all(
			"Credit Transaction",
			filters={"source_payment": payment.name, "docstatus": 1},
			fields=["name", "modified", "docstatus"],
		)[0]

		reversal = self.make_reversal(payment)
		reversal.insert()
		reversal.submit()

		transactions = frappe.get_all(
			"Credit Transaction",
			filters={"source_payment_reversal": reversal.name},
			fields=[
				"docstatus",
				"transaction_type",
				"direction",
				"amount",
				"reversal_of",
				"source_payment_reversal",
			],
		)
		self.assertEqual(len(transactions), 1)
		self.assertEqual(transactions[0].docstatus, 1)
		self.assertEqual(transactions[0].transaction_type, "OPENING_BALANCE")
		self.assertEqual(transactions[0].direction, "DEBIT")
		self.assertEqual(transactions[0].reversal_of, original_transaction.name)
		self.assertEqual(transactions[0].source_payment_reversal, reversal.name)
		self.assertEqual(
			Decimal(str(transactions[0].amount)), Decimal(str(payment.amount))
		)
		self.assertEqual(
			Decimal(self.customer.get_ledger_balance()), balance_before_payment
		)
		self.assertEqual(
			frappe.get_doc("Payment", payment.name).as_dict(), original_payment
		)
		self.assertEqual(
			frappe.db.get_value(
				"Credit Transaction", original_transaction.name, ["modified", "docstatus"]
			),
			(original_transaction.modified, original_transaction.docstatus),
		)

	def test_second_reversal_is_rejected(self):
		payment = self.make_payment()
		first = self.make_reversal(payment)
		first.insert()
		first.submit()
		with self.assertRaises(frappe.ValidationError):
			self.make_reversal(payment).insert()

	def test_draft_payment_is_rejected(self):
		payment = self.make_payment(submit=False)
		with self.assertRaises(frappe.ValidationError):
			self.make_reversal(payment).insert()

	def test_wrong_business_is_rejected(self):
		payment = self.make_payment()
		with self.assertRaises(frappe.ValidationError):
			self.make_reversal(payment, self.other_business.name).insert()

	def test_generated_transaction_failure_has_no_partial_reversal(self):
		payment = self.make_payment()
		reversal = self.make_reversal(payment)
		reversal.insert()

		from creditflow.creditflow.doctype.credit_transaction.credit_transaction import (
			CreditTransaction,
		)

		with patch.object(
			CreditTransaction,
			"submit",
			side_effect=frappe.ValidationError("Forced reversal transaction failure"),
		):
			with self.assertRaises(frappe.ValidationError):
				reversal.submit()

		self.assertEqual(
			frappe.db.count(
				"Credit Transaction", {"source_payment_reversal": reversal.name}
			),
			0,
		)
		self.assertEqual(
			frappe.db.get_value("Payment Reversal", reversal.name, "docstatus"), 0
		)

	def test_submitted_reversal_cannot_be_cancelled(self):
		payment = self.make_payment()
		reversal = self.make_reversal(payment)
		reversal.insert()
		reversal.submit()
		with self.assertRaises(frappe.ValidationError):
			reversal.cancel()
