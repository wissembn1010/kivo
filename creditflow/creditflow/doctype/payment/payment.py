from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document


MONEY_PRECISION = Decimal("0.001")


class Payment(Document):
	def validate(self):
		self.validate_business_and_customer()
		self.validate_amount()

	def validate_business_and_customer(self):
		if not self.business or not frappe.db.exists("Business", self.business):
			frappe.throw(_("Business does not exist."))

		if not self.customer or not frappe.db.exists("Customer", self.customer):
			frappe.throw(_("Customer does not exist."))

		customer_business = frappe.db.get_value("Customer", self.customer, "business")
		if customer_business != self.business:
			frappe.throw(_("Customer must belong to the same Business as the Payment."))

	def validate_amount(self):
		try:
			amount = Decimal(str(self.amount)).quantize(
				MONEY_PRECISION, rounding=ROUND_HALF_UP
			)
		except (InvalidOperation, TypeError, ValueError):
			frappe.throw(_("Amount must be a valid number."))

		if amount <= 0:
			frappe.throw(_("Amount must be greater than 0."))

		self.amount = amount

	def before_submit(self):
		self.validate()
		save_point = "payment_credit_transaction"
		frappe.db.savepoint(save_point)
		try:
			self.create_credit_transaction()
		except Exception:
			frappe.db.rollback(save_point=save_point)
			raise

	def create_credit_transaction(self):
		transaction = frappe.get_doc(
			{
				"doctype": "Credit Transaction",
				"business": self.business,
				"customer": self.customer,
				"transaction_type": "PAYMENT",
				"direction": "CREDIT",
				"amount": self.amount,
				"transaction_date": self.business_date,
				"source_payment": self.name,
			}
		)
		transaction.flags.creditflow_system_generated = True
		transaction.insert(ignore_permissions=True)
		transaction.submit()

	def before_cancel(self):
		frappe.throw(
			_(
				"Posted Payments cannot be cancelled. They must be reversed using a reversal workflow."
			)
		)
