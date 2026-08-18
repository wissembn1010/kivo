import frappe
from frappe import _
from frappe.model.document import Document


class SaleReversal(Document):
	def validate(self):
		original_sale = self.get_original_sale()
		self.validate_original_sale(original_sale)
		self.validate_not_already_reversed()

	def get_original_sale(self):
		if not self.original_sale or not frappe.db.exists("Sale", self.original_sale):
			frappe.throw(_("Original Sale does not exist."))
		return frappe.get_doc("Sale", self.original_sale)

	def validate_original_sale(self, original_sale):
		if original_sale.docstatus != 1:
			frappe.throw(_("Only a submitted Sale can be reversed."))
		if self.business != original_sale.business:
			frappe.throw(_("Sale Reversal Business must match the Original Sale Business."))

	def validate_not_already_reversed(self):
		filters = {"original_sale": self.original_sale, "docstatus": 1}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		if frappe.db.exists("Sale Reversal", filters):
			frappe.throw(_("This Sale has already been reversed."))

	def before_submit(self):
		frappe.db.sql(
			"SELECT name FROM `tabSale` WHERE name = %s FOR UPDATE",
			self.original_sale,
		)
		self.validate()
		original_sale = self.get_original_sale()
		original_transaction = self.get_original_transaction()
		original_movements = self.get_original_movements()

		save_point = "sale_reversal_records"
		frappe.db.savepoint(save_point)
		try:
			self.create_reversal_transaction(original_sale, original_transaction)
			self.create_reversal_movements(original_movements)
		except Exception:
			frappe.db.rollback(save_point=save_point)
			raise

	def get_original_transaction(self):
		transactions = frappe.get_all(
			"Credit Transaction",
			filters={
				"source_sale": self.original_sale,
				"transaction_type": "SALE",
				"direction": "DEBIT",
				"docstatus": 1,
			},
			pluck="name",
		)
		if len(transactions) != 1:
			frappe.throw(_("Original Sale must have exactly one submitted SALE transaction."))
		return transactions[0]

	def get_original_movements(self):
		movements = frappe.get_all(
			"Stock Movement",
			filters={
				"source_sale": self.original_sale,
				"direction": "OUT",
				"movement_reason": "SALE",
				"docstatus": 1,
			},
			fields=["name", "business", "product", "quantity"],
			order_by="creation asc",
		)
		if not movements:
			frappe.throw(_("Original Sale has no submitted SALE stock movements."))
		return movements

	def create_reversal_transaction(self, original_sale, original_transaction):
		transaction = frappe.get_doc(
			{
				"doctype": "Credit Transaction",
				"business": original_sale.business,
				"customer": original_sale.customer,
				"transaction_type": "RETURN",
				"direction": "CREDIT",
				"amount": original_sale.total_amount,
				"transaction_date": self.business_date,
				"reversal_of": original_transaction,
				"source_sale_reversal": self.name,
			}
		)
		transaction.flags.creditflow_system_generated = True
		transaction.insert()
		transaction.submit()

	def create_reversal_movements(self, original_movements):
		for original in original_movements:
			movement = frappe.get_doc(
				{
					"doctype": "Stock Movement",
					"business": original.business,
					"product": original.product,
					"direction": "IN",
					"quantity": original.quantity,
					"movement_reason": "REVERSAL",
					"business_date": self.business_date,
					"reversal_of": original.name,
					"source_sale_reversal": self.name,
				}
			)
			movement.flags.creditflow_system_generated = True
			movement.insert()
			movement.submit()

	def before_cancel(self):
		frappe.throw(_("Submitted Sale Reversals cannot be cancelled."))
