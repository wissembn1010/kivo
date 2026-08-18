from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestStockMovement(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:10]
		self.business = self.make_business(f"Stock Integrity {suffix}")
		self.other_business = self.make_business(f"Other Stock Integrity {suffix}")
		self.product = self.make_product(self.business.name, f"STOCK-{suffix}")
		self.other_product = self.make_product(self.other_business.name, f"OTHER-STOCK-{suffix}")

	def make_business(self, name):
		return frappe.get_doc({"doctype": "Business", "business_name": name}).insert()

	def make_product(self, business, reference):
		return frappe.get_doc({"doctype": "Product", "business": business, "product_name": reference, "reference": reference}).insert()

	def movement(self, **values):
		data = {"doctype": "Stock Movement", "business": self.business.name, "product": self.product.name, "direction": "IN", "quantity": 1, "movement_reason": "OPENING_STOCK", "business_date": frappe.utils.today()}
		data.update(values)
		return frappe.get_doc(data)

	def test_negative_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.movement(quantity=-1).insert()

	def test_zero_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.movement(quantity=0).insert()

	def test_cross_business_product_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.movement(product=self.other_product.name).insert()

	def test_invalid_direction_reason_combinations_are_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.movement(direction="SIDEWAYS").insert()
		with self.assertRaises(frappe.ValidationError):
			self.movement(movement_reason="CUSTOMER_RETURN").insert()
		with self.assertRaises(frappe.ValidationError):
			self.movement(movement_reason="SALE", direction="OUT").insert()

	def test_manual_movement_cannot_carry_system_source(self):
		with self.assertRaises(frappe.ValidationError):
			self.movement(source_sale="does-not-exist").insert()

	def test_opening_stock_supports_signed_legacy_direction(self):
		self.movement(direction="IN").insert().submit()
		self.movement(direction="OUT").insert().submit()
