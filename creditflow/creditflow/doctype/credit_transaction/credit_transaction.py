from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document


EVENT_DIRECTIONS = {"SALE": {"DEBIT"}, "PAYMENT": {"CREDIT"}, "RETURN": {"CREDIT"}, "OPENING_BALANCE": {"DEBIT", "CREDIT"}}
SOURCE_FIELDS = ("source_sale", "source_payment", "source_sale_reversal", "source_payment_reversal")


class CreditTransaction(Document):
	def validate(self):
		self.validate_amount()
		self.validate_direction()
		self.validate_business()
		self.validate_source_integrity()

	def validate_amount(self):
		try:
			amount = Decimal(str(self.amount))
		except (InvalidOperation, TypeError, ValueError):
			frappe.throw(_("Amount must be a valid number."))
		if amount <= 0:
			frappe.throw(_("Amount must be greater than zero."))

	def validate_direction(self):
		allowed = EVENT_DIRECTIONS.get(self.transaction_type)
		if not allowed:
			frappe.throw(_("Invalid Event Type."))
		if self.direction not in allowed:
			frappe.throw(_("Direction {0} is invalid for {1}.").format(self.direction, self.transaction_type))

	def validate_business(self):
		if not self.business or not frappe.db.exists("Business", self.business):
			frappe.throw(_("Business does not exist."))
		if not self.customer or not frappe.db.exists("Customer", self.customer):
			frappe.throw(_("Customer does not exist."))
		if frappe.db.get_value("Customer", self.customer, "business") != self.business:
			frappe.throw(_("Customer and Credit Transaction must belong to the same Business."))

	def validate_source_integrity(self):
		if self.transaction_type == "OPENING_BALANCE":
			if self.source_payment_reversal:
				self.validate_payment_reversal()
			elif any(self.get(field) for field in SOURCE_FIELDS) or self.reversal_of:
				frappe.throw(_("Manual Opening Balances cannot contain system source links."))
			return
		if not self.flags.get("creditflow_system_generated"):
			frappe.throw(_("{0} Credit Transactions can only be created by their source document.").format(self.transaction_type))
		if self.transaction_type == "SALE":
			self.validate_simple_source("Sale", "source_sale", "total_amount")
		elif self.transaction_type == "PAYMENT":
			self.validate_simple_source("Payment", "source_payment", "amount")
		else:
			self.validate_sale_return()

	def require_only_source(self, expected_field):
		if not self.get(expected_field) or any(self.get(field) for field in SOURCE_FIELDS if field != expected_field):
			frappe.throw(_("Credit Transaction source fields do not match its Event Type."))

	def validate_simple_source(self, doctype, source_field, amount_field):
		self.require_only_source(source_field)
		if self.reversal_of:
			frappe.throw(_("Non-reversal Credit Transactions cannot set Reversal Of."))
		source = frappe.get_doc(doctype, self.get(source_field))
		self.validate_common_source_values(source, amount_field)
		self.validate_unique_source(source_field, source.name)

	def validate_payment_reversal(self):
		if not self.flags.get("creditflow_system_generated"):
			frappe.throw(_("Payment reversal ledger entries can only be created by Payment Reversal."))
		self.require_only_source("source_payment_reversal")
		if self.direction != "DEBIT" or not self.reversal_of:
			frappe.throw(_("Payment reversal ledger entries must be DEBIT reversals."))
		reversal = frappe.get_doc("Payment Reversal", self.source_payment_reversal)
		payment = frappe.get_doc("Payment", reversal.original_payment)
		self.validate_common_source_values(payment, "amount")
		self.validate_original_transaction("source_payment", payment.name, "PAYMENT", "CREDIT")
		self.validate_unique_source("source_payment_reversal", reversal.name)

	def validate_sale_return(self):
		self.require_only_source("source_sale_reversal")
		if not self.reversal_of:
			frappe.throw(_("RETURN Credit Transactions must reference the original transaction."))
		reversal = frappe.get_doc("Sale Reversal", self.source_sale_reversal)
		sale = frappe.get_doc("Sale", reversal.original_sale)
		self.validate_common_source_values(sale, "total_amount")
		self.validate_original_transaction("source_sale", sale.name, "SALE", "DEBIT")
		self.validate_unique_source("source_sale_reversal", reversal.name)

	def validate_common_source_values(self, source, amount_field):
		if source.docstatus == 2 or source.business != self.business or source.customer != self.customer:
			frappe.throw(_("Credit Transaction Business and Customer must match its source."))
		if Decimal(str(source.get(amount_field))) != Decimal(str(self.amount)):
			frappe.throw(_("Credit Transaction Amount must match its source."))

	def validate_original_transaction(self, source_field, source_name, transaction_type, direction):
		original = frappe.db.get_value("Credit Transaction", self.reversal_of, ["business", "customer", "amount", "transaction_type", "direction", source_field, "docstatus"], as_dict=True)
		if not original or original.docstatus != 1 or original.business != self.business or original.customer != self.customer or Decimal(str(original.amount)) != Decimal(str(self.amount)) or original.transaction_type != transaction_type or original.direction != direction or original.get(source_field) != source_name:
			frappe.throw(_("Reversal Of does not match the original economic transaction."))

	def validate_unique_source(self, source_field, source_name):
		filters = {source_field: source_name, "docstatus": ["!=", 2]}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		if frappe.db.exists("Credit Transaction", filters):
			frappe.throw(_("A Credit Transaction already exists for this source document."))

	def before_cancel(self):
		frappe.throw(_("Posted ledger entries cannot be cancelled. Create a reversal instead."))
