from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestSale(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Sale Test Business {suffix}"}
		).insert()
		self.other_business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Other Sale Test Business {suffix}"}
		).insert()
		self.customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"Sale Test Customer {suffix}",
				"business": self.business.name,
			}
		).insert()
		self.other_customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"Other Sale Test Customer {suffix}",
				"business": self.other_business.name,
			}
		).insert()
		self.product = self.make_product(self.business.name, f"SALE-TEST-{suffix}")
		self.other_product = self.make_product(
			self.other_business.name, f"SALE-TEST-OTHER-{suffix}"
		)

	def make_product(self, business, reference):
		return frappe.get_doc(
			{
				"doctype": "Product",
				"business": business,
				"product_name": reference,
				"reference": reference,
			}
		).insert()

	def make_sale(self, customer=None, product=None, quantity="1", unit_price="1"):
		sale = frappe.get_doc(
			{
				"doctype": "Sale",
				"business": self.business.name,
				"customer": customer or self.customer.name,
				"business_date": frappe.utils.today(),
			}
		)
		sale.append(
			"items",
			{
				"product": product or self.product.name,
				"quantity": quantity,
				"unit_price": unit_price,
			},
		)
		return sale

	def add_stock(self, product, quantity):
		movement = frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business.name,
				"product": product,
				"direction": "IN",
				"quantity": quantity,
				"movement_reason": "OPENING_STOCK",
				"business_date": frappe.utils.today(),
			}
		).insert()
		movement.submit()
		return movement

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

	def test_valid_sale_and_total_calculation(self):
		sale = self.make_sale(quantity="1.234", unit_price="2.3456")
		sale.validate()
		self.assertEqual(Decimal(str(sale.total_amount)), Decimal("2.894"))

	def test_zero_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_sale(quantity="0").validate()

	def test_product_from_another_business_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_sale(product=self.other_product.name).validate()

	def test_customer_from_another_business_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_sale(customer=self.other_customer.name).validate()

	def test_successful_sale_posts_ledger_and_stock(self):
		self.add_stock(self.product.name, "10")
		sale = self.make_sale(quantity="3", unit_price="2.345")
		sale.insert()
		sale.submit()

		transactions = frappe.get_all(
			"Credit Transaction",
			filters={"source_sale": sale.name},
			fields=["docstatus", "transaction_type", "direction", "amount", "source_sale"],
		)
		self.assertEqual(len(transactions), 1)
		self.assertEqual(transactions[0].docstatus, 1)
		self.assertEqual(transactions[0].transaction_type, "SALE")
		self.assertEqual(transactions[0].direction, "DEBIT")
		self.assertEqual(
			Decimal(str(transactions[0].amount)), Decimal(str(sale.total_amount))
		)
		self.assertEqual(transactions[0].source_sale, sale.name)

		movements = frappe.get_all(
			"Stock Movement",
			filters={"source_sale": sale.name},
			fields=["docstatus", "direction", "quantity", "movement_reason", "source_sale"],
		)
		self.assertEqual(len(movements), 1)
		self.assertEqual(movements[0].docstatus, 1)
		self.assertEqual(movements[0].direction, "OUT")
		self.assertEqual(movements[0].movement_reason, "SALE")
		self.assertEqual(movements[0].source_sale, sale.name)
		self.assertEqual(self.get_stock(self.product.name), Decimal("7"))
		self.assertEqual(
			Decimal(self.customer.get_ledger_balance()), Decimal(str(sale.total_amount))
		)

	def test_insufficient_stock_has_no_side_effects(self):
		self.add_stock(self.product.name, "2")
		sale = self.make_sale(quantity="3")
		sale.insert()

		with self.assertRaises(frappe.ValidationError):
			sale.submit()

		self.assertEqual(
			frappe.db.count("Credit Transaction", {"source_sale": sale.name}), 0
		)
		self.assertEqual(frappe.db.count("Stock Movement", {"source_sale": sale.name}), 0)
		self.assertEqual(self.get_stock(self.product.name), Decimal("2"))

	def test_multiple_products_post_correct_movements(self):
		second_product = self.make_product(
			self.business.name, f"SALE-TEST-SECOND-{uuid4().hex[:10]}"
		)
		self.add_stock(self.product.name, "10")
		self.add_stock(second_product.name, "8")
		sale = self.make_sale(quantity="3", unit_price="2")
		sale.append(
			"items",
			{"product": second_product.name, "quantity": "5", "unit_price": "4"},
		)
		sale.insert()
		sale.submit()

		self.assertEqual(
			frappe.db.count("Stock Movement", {"source_sale": sale.name}), 2
		)
		self.assertEqual(self.get_stock(self.product.name), Decimal("7"))
		self.assertEqual(self.get_stock(second_product.name), Decimal("3"))
		self.assertEqual(Decimal(str(sale.total_amount)), Decimal("26.000"))

	def test_same_product_rows_are_aggregated_for_stock_check(self):
		self.add_stock(self.product.name, "6")
		sale = self.make_sale(quantity="4")
		sale.append(
			"items",
			{"product": self.product.name, "quantity": "3", "unit_price": "1"},
		)
		sale.insert()

		with self.assertRaises(frappe.ValidationError):
			sale.submit()

		self.assertEqual(
			frappe.db.count("Credit Transaction", {"source_sale": sale.name}), 0
		)
		self.assertEqual(frappe.db.count("Stock Movement", {"source_sale": sale.name}), 0)
		self.assertEqual(self.get_stock(self.product.name), Decimal("6"))

	def test_posted_sale_cannot_be_cancelled(self):
		self.add_stock(self.product.name, "2")
		sale = self.make_sale(quantity="1")
		sale.insert()
		sale.submit()

		with self.assertRaises(frappe.ValidationError):
			sale.cancel()
