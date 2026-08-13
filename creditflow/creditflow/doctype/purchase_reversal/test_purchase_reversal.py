from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestPurchaseReversal(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:10]
		self.business = self.make_business(f"Purchase Reversal Business {suffix}")
		self.other_business = self.make_business(
			f"Other Purchase Reversal Business {suffix}"
		)
		self.supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": self.business.name,
				"supplier_name": f"Purchase Reversal Supplier {suffix}",
			}
		).insert()
		self.product = self.make_product(f"PURCHASE-REVERSAL-{suffix}")
		self.second_product = self.make_product(
			f"PURCHASE-REVERSAL-SECOND-{suffix}"
		)

	def make_business(self, business_name):
		return frappe.get_doc(
			{"doctype": "Business", "business_name": business_name}
		).insert()

	def make_product(self, reference):
		return frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business.name,
				"product_name": reference,
				"reference": reference,
			}
		).insert()

	def make_purchase(self, rows=None, submit=True):
		rows = rows or [(self.product.name, "10")]
		purchase = frappe.get_doc(
			{
				"doctype": "Purchase",
				"business": self.business.name,
				"supplier": self.supplier.name,
				"business_date": frappe.utils.today(),
			}
		)
		for product, quantity in rows:
			purchase.append(
				"items",
				{"product": product, "quantity": quantity, "unit_cost": "2"},
			)
		purchase.insert()
		if submit:
			purchase.submit()
		return purchase

	def make_reversal(self, purchase, business=None):
		return frappe.get_doc(
			{
				"doctype": "Purchase Reversal",
				"original_purchase": purchase.name,
				"business": business or purchase.business,
				"business_date": frappe.utils.today(),
				"reason": "Test purchase reversal",
			}
		)

	def add_out_movement(self, product, quantity):
		movement = frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business.name,
				"product": product,
				"direction": "OUT",
				"quantity": quantity,
				"movement_reason": "MANUAL_ADJUSTMENT",
				"business_date": frappe.utils.today(),
			}
		).insert()
		movement.submit()

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

	def submit_reversal(self, purchase):
		reversal = self.make_reversal(purchase)
		reversal.insert()
		reversal.submit()
		return reversal

	def test_successful_reversal_is_append_only(self):
		purchase = self.make_purchase()
		original_purchase = frappe.get_doc("Purchase", purchase.name).as_dict()
		original_movements = frappe.get_all(
			"Stock Movement",
			filters={"source_purchase": purchase.name, "docstatus": 1},
			fields=["name", "modified", "docstatus", "quantity"],
		)
		reversal = self.submit_reversal(purchase)
		movements = frappe.get_all(
			"Stock Movement",
			filters={"source_purchase_reversal": reversal.name},
			fields=[
				"docstatus",
				"direction",
				"movement_reason",
				"quantity",
				"reversal_of",
				"source_purchase_reversal",
			],
		)
		self.assertEqual(len(movements), 1)
		self.assertEqual(movements[0].docstatus, 1)
		self.assertEqual(movements[0].direction, "OUT")
		self.assertEqual(movements[0].movement_reason, "REVERSAL")
		self.assertEqual(movements[0].reversal_of, original_movements[0].name)
		self.assertEqual(movements[0].source_purchase_reversal, reversal.name)
		self.assertEqual(self.get_stock(self.product.name), Decimal("0"))
		self.assertEqual(
			frappe.get_doc("Purchase", purchase.name).as_dict(), original_purchase
		)
		for original in original_movements:
			self.assertEqual(
				frappe.db.get_value(
					"Stock Movement", original.name, ["modified", "docstatus"]
				),
				(original.modified, original.docstatus),
			)

	def test_multiple_products_reverse_correctly(self):
		purchase = self.make_purchase(
			[(self.product.name, "10"), (self.second_product.name, "6")]
		)
		reversal = self.submit_reversal(purchase)
		self.assertEqual(
			frappe.db.count(
				"Stock Movement", {"source_purchase_reversal": reversal.name}
			),
			2,
		)
		self.assertEqual(self.get_stock(self.product.name), Decimal("0"))
		self.assertEqual(self.get_stock(self.second_product.name), Decimal("0"))

	def test_duplicate_product_rows_are_aggregated_for_stock_check(self):
		purchase = self.make_purchase(
			[(self.product.name, "4"), (self.product.name, "3")]
		)
		self.add_out_movement(self.product.name, "1")
		reversal = self.make_reversal(purchase)
		reversal.insert()
		with self.assertRaises(frappe.ValidationError):
			reversal.submit()
		self.assertEqual(
			frappe.db.count(
				"Stock Movement", {"source_purchase_reversal": reversal.name}
			),
			0,
		)

	def test_insufficient_stock_rejects_without_side_effects(self):
		purchase = self.make_purchase([(self.product.name, "10")])
		self.add_out_movement(self.product.name, "6")
		reversal = self.make_reversal(purchase)
		reversal.insert()
		with self.assertRaises(frappe.ValidationError):
			reversal.submit()
		self.assertEqual(self.get_stock(self.product.name), Decimal("4"))
		self.assertEqual(
			frappe.db.count(
				"Stock Movement", {"source_purchase_reversal": reversal.name}
			),
			0,
		)

	def test_second_reversal_is_rejected(self):
		purchase = self.make_purchase()
		self.submit_reversal(purchase)
		with self.assertRaises(frappe.ValidationError):
			self.make_reversal(purchase).insert()

	def test_draft_purchase_is_rejected(self):
		purchase = self.make_purchase(submit=False)
		with self.assertRaises(frappe.ValidationError):
			self.make_reversal(purchase).insert()

	def test_wrong_business_is_rejected(self):
		purchase = self.make_purchase()
		with self.assertRaises(frappe.ValidationError):
			self.make_reversal(purchase, self.other_business.name).insert()

	def test_generated_movement_failure_has_no_partial_reversal(self):
		purchase = self.make_purchase(
			[(self.product.name, "10"), (self.second_product.name, "6")]
		)
		reversal = self.make_reversal(purchase)
		reversal.insert()

		from creditflow.creditflow.doctype.stock_movement.stock_movement import (
			StockMovement,
		)

		original_submit = StockMovement.submit
		calls = 0

		def fail_second_submit(movement):
			nonlocal calls
			calls += 1
			if calls == 2:
				raise frappe.ValidationError("Forced reversal movement failure")
			return original_submit(movement)

		with patch.object(StockMovement, "submit", fail_second_submit):
			with self.assertRaises(frappe.ValidationError):
				reversal.submit()
		self.assertEqual(
			frappe.db.count(
				"Stock Movement", {"source_purchase_reversal": reversal.name}
			),
			0,
		)
		self.assertEqual(
			frappe.db.get_value("Purchase Reversal", reversal.name, "docstatus"), 0
		)

	def test_submitted_reversal_cannot_be_cancelled(self):
		purchase = self.make_purchase()
		reversal = self.submit_reversal(purchase)
		with self.assertRaises(frappe.ValidationError):
			reversal.cancel()
