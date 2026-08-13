import frappe
from frappe import _
from frappe.model.document import Document


class PaymentReversal(Document):
	def validate(self):
		original_payment = self.get_original_payment()
		self.validate_original_payment(original_payment)
		self.validate_not_already_reversed()

	def get_original_payment(self):
		if not self.original_payment or not frappe.db.exists(
			"Payment", self.original_payment
		):
			frappe.throw(_("Original Payment does not exist."))
		return frappe.get_doc("Payment", self.original_payment)

	def validate_original_payment(self, original_payment):
		if original_payment.docstatus != 1:
			frappe.throw(_("Only a submitted Payment can be reversed."))
		if self.business != original_payment.business:
			frappe.throw(
				_("Payment Reversal Business must match the Original Payment Business.")
			)

	def validate_not_already_reversed(self):
		filters = {"original_payment": self.original_payment, "docstatus": 1}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		if frappe.db.exists("Payment Reversal", filters):
			frappe.throw(_("This Payment has already been reversed."))

	def before_submit(self):
		frappe.db.sql(
			"SELECT name FROM `tabPayment` WHERE name = %s FOR UPDATE",
			self.original_payment,
		)
		self.validate()
		original_payment = self.get_original_payment()
		original_transaction = self.get_original_transaction()

		save_point = "payment_reversal_transaction"
		frappe.db.savepoint(save_point)
		try:
			self.create_reversal_transaction(original_payment, original_transaction)
		except Exception:
			frappe.db.rollback(save_point=save_point)
			raise

	def get_original_transaction(self):
		transactions = frappe.get_all(
			"Credit Transaction",
			filters={
				"source_payment": self.original_payment,
				"transaction_type": "PAYMENT",
				"direction": "CREDIT",
				"docstatus": 1,
			},
			pluck="name",
		)
		if len(transactions) != 1:
			frappe.throw(
				_("Original Payment must have exactly one submitted PAYMENT transaction.")
			)
		return transactions[0]

	def create_reversal_transaction(self, original_payment, original_transaction):
		transaction = frappe.get_doc(
			{
				"doctype": "Credit Transaction",
				"business": original_payment.business,
				"customer": original_payment.customer,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": original_payment.amount,
				"transaction_date": self.business_date,
				"reversal_of": original_transaction,
				"source_payment_reversal": self.name,
			}
		)
		transaction.insert()
		transaction.submit()

	def before_cancel(self):
		frappe.throw(_("Submitted Payment Reversals cannot be cancelled."))
