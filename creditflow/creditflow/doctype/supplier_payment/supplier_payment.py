from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document


MONEY_PRECISION = Decimal("0.001")


class SupplierPayment(Document):
	def validate(self):
		self.validate_business_and_supplier()
		self.validate_amount()
		self.validate_payment_method()

	def validate_business_and_supplier(self):
		if not self.business or not frappe.db.exists("Business", self.business):
			frappe.throw(_("Business does not exist."))
		if not self.supplier or not frappe.db.exists("Supplier", self.supplier):
			frappe.throw(_("Supplier does not exist."))
		supplier_business = frappe.db.get_value("Supplier", self.supplier, "business")
		if supplier_business != self.business:
			frappe.throw(_("Supplier must belong to the same Business as the Supplier Payment."))

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

	def validate_payment_method(self):
		allowed = {"CASH", "BANK_TRANSFER", "CHEQUE", "OTHER"}
		if self.payment_method and self.payment_method not in allowed:
			frappe.throw(_("Payment Method is invalid."))

	def before_submit(self):
		self.validate()
		save_point = "supplier_payment_supplier_transaction"
		frappe.db.savepoint(save_point)
		try:
			self.create_supplier_transaction()
		except Exception:
			frappe.db.rollback(save_point=save_point)
			raise

	def create_supplier_transaction(self):
		transaction = frappe.get_doc(
			{
				"doctype": "Supplier Transaction",
				"business": self.business,
				"supplier": self.supplier,
				"transaction_type": "SUPPLIER_PAYMENT",
				"direction": "CREDIT",
				"amount": self.amount,
				"transaction_date": self.business_date,
				"source_supplier_payment": self.name,
			}
		)
		transaction.flags.creditflow_system_generated = True
		transaction.insert(ignore_permissions=True)
		transaction.submit()

	def before_cancel(self):
		frappe.throw(
			_(
				"Posted Supplier Payments cannot be cancelled. They must be reversed using a reversal workflow."
			)
		)

	def before_delete(self):
		if self.docstatus == 1:
			frappe.throw(_("Submitted Supplier Payments cannot be deleted. Use a reversal workflow instead."))
