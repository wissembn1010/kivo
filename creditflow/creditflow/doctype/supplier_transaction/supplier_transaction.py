from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document


EVENT_DIRECTIONS = {
	"PURCHASE": {"DEBIT"},
	"SUPPLIER_PAYMENT": {"CREDIT"},
	"RETURN": {"CREDIT"},
	"PURCHASE_REVERSAL": {"CREDIT"},
	"OPENING_BALANCE": {"DEBIT", "CREDIT"},
}
SOURCE_FIELDS = (
	"source_purchase",
	"source_supplier_payment",
	"source_purchase_reversal",
	"source_supplier_payment_reversal",
)


class SupplierTransaction(Document):
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
			frappe.throw(
				_("Direction {0} is invalid for {1}.").format(self.direction, self.transaction_type)
			)

	def validate_business(self):
		if not self.business or not frappe.db.exists("Business", self.business):
			frappe.throw(_("Business does not exist."))
		if not self.supplier or not frappe.db.exists("Supplier", self.supplier):
			frappe.throw(_("Supplier does not exist."))
		if frappe.db.get_value("Supplier", self.supplier, "business") != self.business:
			frappe.throw(
				_("Supplier and Supplier Transaction must belong to the same Business.")
			)

	def validate_source_integrity(self):
		if self.transaction_type == "OPENING_BALANCE":
			if self.source_supplier_payment_reversal:
				self.validate_supplier_payment_reversal()
			elif any(self.get(field) for field in SOURCE_FIELDS) or self.reversal_of:
				frappe.throw(_("Manual Opening Balances cannot contain system source links."))
			return
		if not self.flags.get("creditflow_system_generated"):
			frappe.throw(
				_("{0} Supplier Transactions can only be created by their source document.").format(
					self.transaction_type
				)
			)
		if self.transaction_type == "PURCHASE":
			self.validate_simple_source("Purchase", "source_purchase", "total_amount")
		elif self.transaction_type == "SUPPLIER_PAYMENT":
			self.validate_simple_source("Supplier Payment", "source_supplier_payment", "amount")
		else:
			self.validate_return()

	def require_only_source(self, expected_field):
		if not self.get(expected_field) or any(
			self.get(field) for field in SOURCE_FIELDS if field != expected_field
		):
			frappe.throw(_("Supplier Transaction source fields do not match its Event Type."))

	def validate_simple_source(self, doctype, source_field, amount_field):
		self.require_only_source(source_field)
		if self.reversal_of:
			frappe.throw(_("Non-reversal Supplier Transactions cannot set Reversal Of."))
		source = frappe.get_doc(doctype, self.get(source_field))
		self.validate_common_source_values(source, amount_field)
		self.validate_unique_source(source_field, source.name)

	def validate_supplier_payment_reversal(self):
		if not self.flags.get("creditflow_system_generated"):
			frappe.throw(
				_("Supplier payment reversal ledger entries can only be created by Supplier Payment Reversal.")
			)
		self.require_only_source("source_supplier_payment_reversal")
		if self.direction != "DEBIT" or not self.reversal_of:
			frappe.throw(_("Supplier payment reversal ledger entries must be DEBIT reversals."))
		reversal = frappe.get_doc("Supplier Payment Reversal", self.source_supplier_payment_reversal)
		payment = frappe.get_doc("Supplier Payment", reversal.original_supplier_payment)
		self.validate_common_source_values(payment, "amount")
		self.validate_original_transaction(
			"source_supplier_payment", payment.name, "SUPPLIER_PAYMENT", "CREDIT"
		)
		self.validate_unique_source("source_supplier_payment_reversal", reversal.name)

	def validate_return(self):
		self.require_only_source("source_purchase_reversal")
		if not self.reversal_of:
			frappe.throw(_("RETURN Supplier Transactions must reference the original transaction."))
		reversal = frappe.get_doc("Purchase Reversal", self.source_purchase_reversal)
		purchase = frappe.get_doc("Purchase", reversal.original_purchase)
		self.validate_common_source_values(purchase, "total_amount")
		self.validate_original_transaction("source_purchase", purchase.name, "PURCHASE", "DEBIT")
		self.validate_unique_source("source_purchase_reversal", reversal.name)

	def validate_common_source_values(self, source, amount_field):
		if source.docstatus == 2 or source.business != self.business or source.supplier != self.supplier:
			frappe.throw(_("Supplier Transaction Business and Supplier must match its source."))
		if Decimal(str(source.get(amount_field))) != Decimal(str(self.amount)):
			frappe.throw(_("Supplier Transaction Amount must match its source."))

	def validate_original_transaction(
		self, source_field, source_name, transaction_type, direction
	):
		original = frappe.db.get_value(
			"Supplier Transaction",
			self.reversal_of,
			[
				"business",
				"supplier",
				"amount",
				"transaction_type",
				"direction",
				source_field,
				"docstatus",
			],
			as_dict=True,
		)
		if (
			not original
			or original.docstatus != 1
			or original.business != self.business
			or original.supplier != self.supplier
			or Decimal(str(original.amount)) != Decimal(str(self.amount))
			or original.transaction_type != transaction_type
			or original.direction != direction
			or original.get(source_field) != source_name
		):
			frappe.throw(_("Reversal Of does not match the original economic transaction."))

	def validate_unique_source(self, source_field, source_name):
		filters = {source_field: source_name, "docstatus": ["!=", 2]}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		if frappe.db.exists("Supplier Transaction", filters):
			frappe.throw(_("A Supplier Transaction already exists for this source document."))

	def before_cancel(self):
		frappe.throw(_("Posted ledger entries cannot be cancelled. Create a reversal instead."))
