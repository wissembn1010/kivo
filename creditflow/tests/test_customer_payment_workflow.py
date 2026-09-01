from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestCustomerPaymentWorkflow(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.suffix = uuid4().hex[:10]
		self.business = self.make_business(f"Payment Workflow {self.suffix}")
		self.other_business = self.make_business(f"Other Payment Workflow {self.suffix}")
		self.customer = self.make_customer(self.business.name, "Customer")
		self.other_customer = self.make_customer(self.other_business.name, "Other Customer")
		self.staff = self.make_user("staff", self.business.name, "STAFF")

	def tearDown(self):
		frappe.set_user("Administrator")

	def make_business(self, name):
		return frappe.get_doc({"doctype": "Business", "business_name": name}).insert()

	def make_customer(self, business, label):
		return frappe.get_doc(
			{
				"doctype": "Customer",
				"business": business,
				"customer_name": f"{label} {self.suffix}",
			}
		).insert()

	def make_user(self, prefix, business, role):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"payment-{prefix}-{self.suffix}@example.com",
				"first_name": prefix,
				"send_welcome_email": 0,
				"creditflow_business": business,
			}
		).insert(ignore_permissions=True)
		user.add_roles(role)
		return user.name

	def add_debt(self, amount="100.000"):
		frappe.set_user("Administrator")
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
		return transaction

	def make_payment(
		self,
		amount="25.000",
		payment_method="CASH",
		reference=None,
		notes=None,
		business=None,
		customer=None,
		submit=True,
	):
		payment = frappe.get_doc(
			{
				"doctype": "Payment",
				"business": business or self.business.name,
				"customer": customer or self.customer.name,
				"business_date": frappe.utils.today(),
				"amount": amount,
				"payment_method": payment_method,
				"reference": reference,
				"notes": notes,
			}
		).insert()
		if submit:
			payment.submit()
		return payment

	def balance(self):
		return Decimal(self.customer.get_ledger_balance())

	def test_cash_payment_reference_is_optional(self):
		self.add_debt()
		payment = self.make_payment(payment_method="CASH")
		self.assertEqual(payment.payment_method, "CASH")
		self.assertEqual(payment.reference, "")

	def test_bank_transfer_payment_preserves_reference(self):
		self.add_debt()
		payment = self.make_payment(
			payment_method="BANK_TRANSFER", reference="VIR-2026-001"
		)
		self.assertEqual(payment.payment_method, "BANK_TRANSFER")
		self.assertEqual(payment.reference, "VIR-2026-001")

	def test_cheque_payment_requires_and_preserves_reference(self):
		self.add_debt()
		with self.assertRaises(frappe.ValidationError):
			self.make_payment(payment_method="CHEQUE", reference=None)
		payment = self.make_payment(payment_method="CHEQUE", reference="CHQ-7788")
		self.assertEqual(payment.reference, "CHQ-7788")

	def test_card_and_other_payment_methods_are_supported(self):
		self.add_debt()
		card = self.make_payment(amount="10", payment_method="CARD")
		other = self.make_payment(amount="10", payment_method="OTHER", reference="MISC-1")
		self.assertEqual(card.payment_method, "CARD")
		self.assertEqual(other.payment_method, "OTHER")
		self.assertEqual(other.reference, "MISC-1")

	def test_exact_settlement_succeeds_and_overpayment_is_rejected(self):
		self.add_debt("25.125")
		payment = self.make_payment("25.125")
		self.assertEqual(self.balance(), Decimal("0.000"))
		self.assertEqual(Decimal(str(payment.amount)), Decimal("25.125"))

		with self.assertRaisesRegex(
			frappe.ValidationError, "exceeds the customer's outstanding debt"
		):
			self.make_payment("0.001")

	def test_overpayment_is_rejected_without_ledger_effect(self):
		self.add_debt("10.000")
		with self.assertRaisesRegex(
			frappe.ValidationError, "exceeds the customer's outstanding debt"
		):
			self.make_payment("10.001")
		self.assertEqual(self.balance(), Decimal("10.000"))
		self.assertEqual(frappe.db.count("Payment", {"customer": self.customer.name}), 0)

	def test_payment_posts_exactly_one_credit_effect_and_reversal_restores_debt(self):
		self.add_debt("50.000")
		payment = self.make_payment("20.000", payment_method="BANK_TRANSFER", reference="VIR-2")
		transactions = frappe.get_all(
			"Credit Transaction",
			filters={"source_payment": payment.name},
			fields=["name", "direction", "amount", "docstatus"],
		)
		self.assertEqual(len(transactions), 1)
		self.assertEqual(transactions[0].direction, "CREDIT")
		self.assertEqual(Decimal(str(transactions[0].amount)), Decimal("20.000"))
		self.assertEqual(self.balance(), Decimal("30.000"))

		reversal = frappe.get_doc(
			{
				"doctype": "Payment Reversal",
				"business": self.business.name,
				"original_payment": payment.name,
				"business_date": frappe.utils.today(),
				"reason": "Payment workflow test",
			}
		).insert()
		reversal.submit()
		self.assertEqual(self.balance(), Decimal("50.000"))
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Payment Reversal",
					"business": self.business.name,
					"original_payment": payment.name,
					"business_date": frappe.utils.today(),
					"reason": "Duplicate",
				}
			).insert()

	def test_cross_business_payment_creation_is_denied(self):
		frappe.set_user(self.staff)
		with self.assertRaises(frappe.PermissionError):
			self.make_payment(
				business=self.other_business.name,
				customer=self.other_customer.name,
			)

	def test_staff_can_submit_payment_but_cannot_create_credit_transaction(self):
		self.add_debt()
		frappe.set_user(self.staff)
		payment = self.make_payment("15.000", payment_method="CARD")
		self.assertEqual(
			frappe.db.count(
				"Credit Transaction", {"source_payment": payment.name, "docstatus": 1}
			),
			1,
		)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Credit Transaction",
					"business": self.business.name,
					"customer": self.customer.name,
					"transaction_type": "OPENING_BALANCE",
					"direction": "DEBIT",
					"amount": 1,
					"transaction_date": frappe.utils.today(),
				}
			).insert()

	def test_payment_receipt_contains_operational_payment_data(self):
		self.add_debt()
		payment = self.make_payment(
			"25.125",
			payment_method="CHEQUE",
			reference="CHQ-12345",
			notes="Received at front desk",
		)
		html = frappe.get_print("Payment", payment.name, "Payment Receipt")
		for expected in (
			self.business.business_name,
			self.customer.customer_name,
			payment.name,
			payment.get_formatted("business_date"),
			payment.get_formatted("amount"),
			"Cheque",
			"CHQ-12345",
			"Received at front desk",
		):
			self.assertIn(str(expected), html)

	def test_payment_history_fields_are_visible_in_native_list(self):
		meta = frappe.get_meta("Payment")
		for fieldname in (
			"customer",
			"business_date",
			"amount",
			"payment_method",
			"reference",
		):
			self.assertTrue(meta.get_field(fieldname).in_list_view, fieldname)
