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

	def test_manual_out_cannot_exceed_stock_or_borrow_foreign_stock(self):
		self.movement().insert().submit()
		self.movement(business=self.other_business.name, product=self.other_product.name, quantity=100).insert().submit()
		for reason in ("MANUAL_ADJUSTMENT", "OPENING_STOCK"):
			with self.subTest(reason=reason):
				draft = self.movement(direction="OUT", quantity=2, movement_reason=reason).insert()
				before = frappe.db.count("Stock Movement", {"business": self.business.name, "docstatus": 1})
				with self.assertRaisesRegex(frappe.ValidationError, "Insufficient stock"):
					draft.submit()
				self.assertEqual(frappe.db.get_value("Stock Movement", draft.name, "docstatus"), 0)
				self.assertEqual(frappe.db.count("Stock Movement", {"business": self.business.name, "docstatus": 1}), before)

	def test_direct_submitted_insert_cannot_bypass_manual_out_check(self):
		self.movement().insert().submit()
		before = frappe.db.count("Stock Movement", {"business": self.business.name})
		with self.assertRaisesRegex(frappe.ValidationError, "Insufficient stock"):
			self.movement(direction="OUT", quantity=2, movement_reason="MANUAL_ADJUSTMENT", docstatus=1).insert()
		self.assertEqual(frappe.db.count("Stock Movement", {"business": self.business.name}), before)

	def test_valid_fractional_manual_out_can_reach_zero_not_below(self):
		self.movement().insert().submit()
		for unused in range(2):
			self.movement(direction="OUT", quantity=0.5, movement_reason="MANUAL_ADJUSTMENT").insert().submit()
		with self.assertRaisesRegex(frappe.ValidationError, "Insufficient stock"):
			self.movement(direction="OUT", quantity=0.001).insert().submit()

	def test_manual_out_draft_can_wait_for_stock_before_posting(self):
		draft = self.movement(direction="OUT", movement_reason="MANUAL_ADJUSTMENT").insert()
		with self.assertRaisesRegex(frappe.ValidationError, "Insufficient stock"):
			draft.submit()
		self.assertEqual(frappe.db.get_value("Stock Movement", draft.name, "docstatus"), 0)
		self.movement().insert().submit()
		frappe.get_doc("Stock Movement", draft.name).submit()
		self.assertEqual(frappe.db.get_value("Stock Movement", draft.name, "docstatus"), 1)
