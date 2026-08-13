from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestPurchase(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:10]
		self.business = self.make_business(f"Purchase Test Business {suffix}")
		self.other_business = self.make_business(f"Other Purchase Test Business {suffix}")
		self.supplier = self.make_supplier(
			self.business.name, f"Purchase Test Supplier {suffix}"
		)
		self.other_supplier = self.make_supplier(
			self.other_business.name, f"Other Purchase Test Supplier {suffix}"
		)
		self.product = self.make_product(
			self.business.name, f"PURCHASE-TEST-{suffix}"
		)
		self.other_product = self.make_product(
			self.other_business.name, f"PURCHASE-TEST-OTHER-{suffix}"
		)

	def make_business(self, business_name):
		return frappe.get_doc(
			{"doctype": "Business", "business_name": business_name}
		).insert()

	def make_supplier(self, business, supplier_name):
		return frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": business,
				"supplier_name": supplier_name,
			}
		).insert()

	def make_product(self, business, reference):
		return frappe.get_doc(
			{
				"doctype": "Product",
				"business": business,
				"product_name": reference,
				"reference": reference,
			}
		).insert()

	def make_purchase(
		self, supplier=None, product=None, quantity="2", unit_cost="3.4567"
	):
		purchase = frappe.get_doc(
			{
				"doctype": "Purchase",
				"business": self.business.name,
				"supplier": supplier or self.supplier.name,
				"business_date": frappe.utils.today(),
			}
		)
		purchase.append(
			"items",
			{
				"product": product or self.product.name,
				"quantity": quantity,
				"unit_cost": unit_cost,
			},
		)
		return purchase

	def get_stock(self, product):
		result = frappe.db.sql(
			"""
			SELECT COALESCE(
				SUM(CASE WHEN direction = 'IN' THEN quantity ELSE -quantity END),
				0
			)
			FROM `tabStock Movement`
			WHERE business = %s AND product = %s AND docstatus = 1
			""",
			(self.business.name, product),
		)
		return Decimal(str(result[0][0] or 0))

	def test_successful_purchase_posts_stock_only(self):
		credit_transactions_before = frappe.db.count("Credit Transaction")
		purchase = self.make_purchase()
		purchase.insert()
		purchase.submit()

		movements = frappe.get_all(
			"Stock Movement",
			filters={"source_purchase": purchase.name},
			fields=[
				"docstatus",
				"direction",
				"quantity",
				"movement_reason",
				"source_purchase",
			],
		)
		self.assertEqual(purchase.docstatus, 1)
		self.assertEqual(len(movements), 1)
		self.assertEqual(movements[0].docstatus, 1)
		self.assertEqual(movements[0].direction, "IN")
		self.assertEqual(movements[0].movement_reason, "PURCHASE")
		self.assertEqual(movements[0].source_purchase, purchase.name)
		self.assertEqual(self.get_stock(self.product.name), Decimal("2"))
		self.assertEqual(
			frappe.db.count("Credit Transaction"), credit_transactions_before
		)
		self.assertEqual(Decimal(str(purchase.total_amount)), Decimal("6.913"))

	def test_multiple_products_post_correct_movements(self):
		second_product = self.make_product(
			self.business.name, f"PURCHASE-TEST-SECOND-{uuid4().hex[:10]}"
		)
		purchase = self.make_purchase(quantity="3", unit_cost="2")
		purchase.append(
			"items",
			{"product": second_product.name, "quantity": "5", "unit_cost": "4"},
		)
		purchase.insert()
		purchase.submit()

		self.assertEqual(
			frappe.db.count("Stock Movement", {"source_purchase": purchase.name}), 2
		)
		self.assertEqual(self.get_stock(self.product.name), Decimal("3"))
		self.assertEqual(self.get_stock(second_product.name), Decimal("5"))
		self.assertEqual(Decimal(str(purchase.total_amount)), Decimal("26.000"))

	def test_zero_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(quantity="0").validate()

	def test_negative_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(quantity="-1").validate()

	def test_supplier_from_another_business_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(supplier=self.other_supplier.name).validate()

	def test_product_from_another_business_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_purchase(product=self.other_product.name).validate()

	def test_stock_movement_failure_has_no_partial_side_effects(self):
		second_product = self.make_product(
			self.business.name, f"PURCHASE-TEST-FAILURE-{uuid4().hex[:10]}"
		)
		purchase = self.make_purchase()
		purchase.append(
			"items",
			{"product": second_product.name, "quantity": "1", "unit_cost": "1"},
		)
		purchase.insert()

		from creditflow.creditflow.doctype.stock_movement.stock_movement import (
			StockMovement,
		)

		original_submit = StockMovement.submit
		calls = 0

		def fail_second_submit(movement):
			nonlocal calls
			calls += 1
			if calls == 2:
				raise frappe.ValidationError("Forced movement failure")
			return original_submit(movement)

		with patch.object(StockMovement, "submit", fail_second_submit):
			with self.assertRaises(frappe.ValidationError):
				purchase.submit()

		self.assertEqual(
			frappe.db.count("Stock Movement", {"source_purchase": purchase.name}), 0
		)
		self.assertEqual(frappe.db.get_value("Purchase", purchase.name, "docstatus"), 0)

	def test_posted_purchase_cannot_be_cancelled(self):
		purchase = self.make_purchase()
		purchase.insert()
		purchase.submit()

		with self.assertRaises(frappe.ValidationError):
			purchase.cancel()
