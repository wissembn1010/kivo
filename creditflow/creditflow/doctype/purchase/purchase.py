from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document


MONEY_PRECISION = Decimal("0.001")


class Purchase(Document):
	def validate(self):
		self.validate_business_and_supplier()
		self.validate_items_and_calculate_total()

	def validate_business_and_supplier(self):
		if not self.business or not frappe.db.exists("Business", self.business):
			frappe.throw(_("Business does not exist."))

		if not self.supplier or not frappe.db.exists("Supplier", self.supplier):
			frappe.throw(_("Supplier does not exist."))

		supplier_business = frappe.db.get_value("Supplier", self.supplier, "business")
		if supplier_business != self.business:
			frappe.throw(_("Supplier must belong to the same Business as the Purchase."))

	def validate_items_and_calculate_total(self):
		if not self.items:
			frappe.throw(_("Purchase must contain at least one item."))

		total = Decimal("0")
		for row in self.items:
			product_business = frappe.db.get_value("Product", row.product, "business")
			if not product_business:
				frappe.throw(_("Product in row {0} does not exist.").format(row.idx))
			if product_business != self.business:
				frappe.throw(
					_(
						"Product in row {0} must belong to the same Business as the Purchase."
					).format(row.idx)
				)

			quantity = self.to_decimal(row.quantity, _("Quantity"), row.idx)
			unit_cost = self.to_decimal(row.unit_cost, _("Unit Cost"), row.idx)
			if quantity <= 0:
				frappe.throw(_("Quantity in row {0} must be greater than 0.").format(row.idx))
			if unit_cost < 0:
				frappe.throw(
					_("Unit Cost in row {0} must be greater than or equal to 0.").format(
						row.idx
					)
				)

			total += quantity * unit_cost

		self.total_amount = total.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)

	def before_submit(self):
		self.validate()
		save_point = "purchase_stock_and_supplier_transaction"
		frappe.db.savepoint(save_point)
		try:
			self.create_stock_movements()
			self.create_supplier_transaction()
		except Exception:
			frappe.db.rollback(save_point=save_point)
			raise

	def create_stock_movements(self):
		for row in self.items:
			movement = frappe.get_doc(
				{
					"doctype": "Stock Movement",
					"business": self.business,
					"product": row.product,
					"direction": "IN",
					"quantity": row.quantity,
					"movement_reason": "PURCHASE",
					"business_date": self.business_date,
					"source_purchase": self.name,
				}
			)
			movement.flags.creditflow_system_generated = True
			movement.insert(ignore_permissions=True)
			movement.submit()

	def create_supplier_transaction(self):
		transaction = frappe.get_doc(
			{
				"doctype": "Supplier Transaction",
				"business": self.business,
				"supplier": self.supplier,
				"transaction_type": "PURCHASE",
				"direction": "DEBIT",
				"amount": self.total_amount,
				"transaction_date": self.business_date,
				"source_purchase": self.name,
			}
		)
		transaction.flags.creditflow_system_generated = True
		transaction.insert(ignore_permissions=True)
		transaction.submit()

	def before_cancel(self):
		frappe.throw(
			_(
				"Posted Purchases cannot be cancelled. They must be reversed using a reversal workflow."
			)
		)

	@staticmethod
	def to_decimal(value, label, row_index):
		try:
			return Decimal(str(value))
		except (InvalidOperation, TypeError, ValueError):
			frappe.throw(_("{0} in row {1} must be a valid number.").format(label, row_index))
