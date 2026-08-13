from decimal import Decimal

import frappe
from frappe import _
from frappe.model.document import Document


class PurchaseReversal(Document):
	def validate(self):
		original_purchase = self.get_original_purchase()
		self.validate_original_purchase(original_purchase)
		self.validate_not_already_reversed()

	def get_original_purchase(self):
		if not self.original_purchase or not frappe.db.exists(
			"Purchase", self.original_purchase
		):
			frappe.throw(_("Original Purchase does not exist."))
		return frappe.get_doc("Purchase", self.original_purchase)

	def validate_original_purchase(self, original_purchase):
		if original_purchase.docstatus != 1:
			frappe.throw(_("Only a submitted Purchase can be reversed."))
		if self.business != original_purchase.business:
			frappe.throw(
				_("Purchase Reversal Business must match the Original Purchase Business.")
			)

	def validate_not_already_reversed(self):
		filters = {"original_purchase": self.original_purchase, "docstatus": 1}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		if frappe.db.exists("Purchase Reversal", filters):
			frappe.throw(_("This Purchase has already been reversed."))

	def before_submit(self):
		frappe.db.sql(
			"SELECT name FROM `tabPurchase` WHERE name = %s FOR UPDATE",
			self.original_purchase,
		)
		self.validate()
		original_movements = self.get_original_movements()
		required_by_product = self.get_required_quantity_by_product(original_movements)
		self.lock_products(required_by_product)
		self.validate_available_stock(required_by_product)

		save_point = "purchase_reversal_movements"
		frappe.db.savepoint(save_point)
		try:
			self.create_reversal_movements(original_movements)
		except Exception:
			frappe.db.rollback(save_point=save_point)
			raise

	def get_original_movements(self):
		movements = frappe.get_all(
			"Stock Movement",
			filters={
				"source_purchase": self.original_purchase,
				"direction": "IN",
				"movement_reason": "PURCHASE",
				"docstatus": 1,
			},
			fields=["name", "business", "product", "quantity"],
			order_by="creation asc",
		)
		if not movements:
			frappe.throw(_("Original Purchase has no submitted PURCHASE stock movements."))
		return movements

	def get_required_quantity_by_product(self, original_movements):
		required_by_product = {}
		for movement in original_movements:
			required_by_product.setdefault(movement.product, Decimal("0"))
			required_by_product[movement.product] += Decimal(str(movement.quantity))
		return required_by_product

	def lock_products(self, required_by_product):
		for product in sorted(required_by_product):
			frappe.db.sql(
				"SELECT name FROM `tabProduct` WHERE name = %s FOR UPDATE",
				product,
			)

	def validate_available_stock(self, required_by_product):
		for product, required_quantity in required_by_product.items():
			available_quantity = self.get_available_stock(product)
			if available_quantity < required_quantity:
				frappe.throw(
					_(
						"Insufficient stock for Product {0}: available {1}, required {2}."
					).format(product, available_quantity, required_quantity)
				)

	def get_available_stock(self, product):
		result = frappe.db.sql(
			"""
			SELECT COALESCE(
				SUM(
					CASE
						WHEN direction = 'IN' THEN quantity
						WHEN direction = 'OUT' THEN -quantity
						ELSE 0
					END
				),
				0
			)
			FROM `tabStock Movement`
			WHERE business = %s
			  AND product = %s
			  AND docstatus = 1
			""",
			(self.business, product),
		)
		return Decimal(str(result[0][0] or 0))

	def create_reversal_movements(self, original_movements):
		for original in original_movements:
			movement = frappe.get_doc(
				{
					"doctype": "Stock Movement",
					"business": original.business,
					"product": original.product,
					"direction": "OUT",
					"quantity": original.quantity,
					"movement_reason": "REVERSAL",
					"business_date": self.business_date,
					"reversal_of": original.name,
					"source_purchase_reversal": self.name,
				}
			)
			movement.insert()
			movement.submit()

	def before_cancel(self):
		frappe.throw(_("Submitted Purchase Reversals cannot be cancelled."))
