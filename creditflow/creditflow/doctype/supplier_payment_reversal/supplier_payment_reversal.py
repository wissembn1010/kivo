from decimal import Decimal

import frappe
from frappe import _
from frappe.model.document import Document


class SupplierPaymentReversal(Document):
	def validate(self):
		original_payment = self.get_original_payment()
		self.validate_original_payment(original_payment)
		self.validate_not_already_reversed()

	def get_original_payment(self):
		if not self.original_supplier_payment or not frappe.db.exists(
			"Supplier Payment", self.original_supplier_payment
		):
			frappe.throw(_("Original Supplier Payment does not exist."))
		return frappe.get_doc("Supplier Payment", self.original_supplier_payment)

	def validate_original_payment(self, original_payment):
		if original_payment.docstatus != 1:
			frappe.throw(_("Only a submitted Supplier Payment can be reversed."))
		if self.business != original_payment.business:
			frappe.throw(
				_("Supplier Payment Reversal Business must match the Original Supplier Payment Business.")
			)

	def validate_not_already_reversed(self):
		filters = {"original_supplier_payment": self.original_supplier_payment, "docstatus": 1}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		if frappe.db.exists("Supplier Payment Reversal", filters):
			frappe.throw(_("This Supplier Payment has already been reversed."))

	def before_submit(self):
		frappe.db.sql(
			"SELECT name FROM `tabSupplier Payment` WHERE name = %s FOR UPDATE",
			self.original_supplier_payment,
		)
		self.validate()
		original_payment = self.get_original_payment()
		original_transaction = self.get_original_transaction()

		save_point = "supplier_payment_reversal_transaction"
		frappe.db.savepoint(save_point)
		try:
			self.create_reversal_transaction(original_payment, original_transaction)
		except Exception:
			frappe.db.rollback(save_point=save_point)
			raise

	def get_original_transaction(self):
		transactions = frappe.get_all(
			"Supplier Transaction",
			filters={
				"source_supplier_payment": self.original_supplier_payment,
				"transaction_type": "SUPPLIER_PAYMENT",
				"direction": "CREDIT",
				"docstatus": 1,
			},
			pluck="name",
		)
		if len(transactions) != 1:
			frappe.throw(
				_("Original Supplier Payment must have exactly one submitted SUPPLIER_PAYMENT transaction.")
			)
		return transactions[0]

	def create_reversal_transaction(self, original_payment, original_transaction):
		transaction = frappe.get_doc(
			{
				"doctype": "Supplier Transaction",
				"business": original_payment.business,
				"supplier": original_payment.supplier,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": original_payment.amount,
				"transaction_date": self.business_date,
				"reversal_of": original_transaction,
				"source_supplier_payment_reversal": self.name,
			}
		)
		transaction.flags.creditflow_system_generated = True
		transaction.insert(ignore_permissions=True)
		transaction.submit()

	def before_cancel(self):
		frappe.throw(_("Submitted Supplier Payment Reversals cannot be cancelled."))
