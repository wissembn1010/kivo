from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestUOMWorkflows(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:10]
		self.business = self.make_business(f"UOM Business {suffix}")
		self.other_business = self.make_business(f"Other UOM Business {suffix}")
		self.customer = self.make_customer(self.business.name, f"UOM Customer {suffix}")
		self.supplier = self.make_supplier(self.business.name, f"UOM Supplier {suffix}")
		self.product = self.make_product(self.business.name, f"UOM-{suffix}", factor="12")
		self.other_product = self.make_product(
			self.other_business.name, f"OTHER-UOM-{suffix}", factor="12"
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def make_business(self, business_name):
		return frappe.get_doc(
			{"doctype": "Business", "business_name": business_name}
		).insert()

	def make_customer(self, business, customer_name):
		return frappe.get_doc(
			{
				"doctype": "Customer",
				"business": business,
				"customer_name": customer_name,
			}
		).insert()

	def make_supplier(self, business, supplier_name):
		return frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": business,
				"supplier_name": supplier_name,
			}
		).insert()

	def make_product(self, business, reference, factor=None):
		product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": business,
				"product_name": reference,
				"reference": reference,
				"primary_unit": "UNIT",
			}
		)
		if factor is not None:
			product.append(
				"uom_conversions",
				{"uom": "BOX", "conversion_factor": factor},
			)
		return product.insert()

	def make_purchase(self, quantity, uom=None, product=None, submit=True):
		purchase = frappe.get_doc(
			{
				"doctype": "Purchase",
				"business": self.business.name,
				"supplier": self.supplier.name,
				"business_date": frappe.utils.today(),
				"items": [
					{
						"product": product or self.product.name,
						"quantity": quantity,
						"uom": uom,
						"unit_cost": "2",
					}
				],
			}
		).insert()
		if submit:
			purchase.submit()
		return purchase

	def make_sale(self, quantity, uom=None, submit=True):
		sale = frappe.get_doc(
			{
				"doctype": "Sale",
				"business": self.business.name,
				"customer": self.customer.name,
				"business_date": frappe.utils.today(),
				"sale_mode": "CREDIT",
				"amount_paid": 0,
				"items": [
					{
						"product": self.product.name,
						"quantity": quantity,
						"uom": uom,
						"unit_price": "3",
					}
				],
			}
		).insert()
		if submit:
			sale.submit()
		return sale

	def add_stock(self, quantity):
		frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business.name,
				"product": self.product.name,
				"direction": "IN",
				"quantity": quantity,
				"movement_reason": "OPENING_STOCK",
				"business_date": frappe.utils.today(),
			}
		).insert().submit()

	def get_stock(self):
		result = frappe.db.sql(
			"""
			SELECT COALESCE(
				SUM(CASE WHEN direction = 'IN' THEN quantity ELSE -quantity END),
				0
			)
			FROM `tabStock Movement`
			WHERE business = %s AND product = %s AND docstatus = 1
			""",
			(self.business.name, self.product.name),
		)
		return Decimal(str(result[0][0] or 0))

	def assert_snapshot(self, document, uom, quantity, factor, base_quantity):
		row = document.items[0]
		self.assertEqual(row.uom, uom)
		self.assertEqual(Decimal(str(row.quantity)), Decimal(quantity))
		self.assertEqual(Decimal(str(row.conversion_factor)), Decimal(factor))
		self.assertEqual(Decimal(str(row.base_quantity)), Decimal(base_quantity))

	def test_primary_uom_purchase_snapshots_and_posts_base_quantity(self):
		purchase = self.make_purchase("10")
		self.assert_snapshot(purchase, "UNIT", "10", "1", "10")
		self.assertEqual(self.get_stock(), Decimal("10"))

	def test_alternate_uom_purchase_snapshots_and_posts_base_quantity(self):
		purchase = self.make_purchase("10", "BOX")
		self.assert_snapshot(purchase, "BOX", "10", "12", "120")
		self.assertEqual(self.get_stock(), Decimal("120"))

	def test_primary_uom_sale_snapshots_and_posts_base_quantity(self):
		self.add_stock("10")
		sale = self.make_sale("2")
		self.assert_snapshot(sale, "UNIT", "2", "1", "2")
		self.assertEqual(self.get_stock(), Decimal("8"))

	def test_alternate_uom_sale_snapshots_and_posts_base_quantity(self):
		self.add_stock("120")
		sale = self.make_sale("2", "BOX")
		self.assert_snapshot(sale, "BOX", "2", "12", "24")
		self.assertEqual(self.get_stock(), Decimal("96"))

	def test_alternate_uom_sale_checks_base_quantity_for_available_stock(self):
		self.add_stock("23")
		sale = self.make_sale("2", "BOX", submit=False)
		with self.assertRaises(frappe.ValidationError):
			sale.submit()
		self.assertEqual(self.get_stock(), Decimal("23"))
		self.assertEqual(
			frappe.db.count("Stock Movement", {"source_sale": sale.name}), 0
		)

	def test_sale_reversal_restores_original_alternate_uom_base_quantity(self):
		self.add_stock("120")
		sale = self.make_sale("2", "BOX")
		reversal = frappe.get_doc(
			{
				"doctype": "Sale Reversal",
				"business": self.business.name,
				"original_sale": sale.name,
				"business_date": frappe.utils.today(),
				"reason": "UOM test",
			}
		).insert()
		reversal.submit()
		self.assertEqual(self.get_stock(), Decimal("120"))
		self.assertEqual(
			Decimal(
				str(
					frappe.db.get_value(
						"Stock Movement",
						{"source_sale_reversal": reversal.name},
						"quantity",
					)
				)
			),
			Decimal("24"),
		)

	def test_conversion_change_does_not_change_historical_purchase_or_reversal(self):
		purchase = self.make_purchase("10", "BOX")
		self.product.reload()
		self.product.uom_conversions[0].conversion_factor = "20"
		self.product.save()

		purchase.reload()
		self.assert_snapshot(purchase, "BOX", "10", "12", "120")
		self.assertEqual(self.get_stock(), Decimal("120"))

		reversal = frappe.get_doc(
			{
				"doctype": "Purchase Reversal",
				"business": self.business.name,
				"original_purchase": purchase.name,
				"business_date": frappe.utils.today(),
				"reason": "Historical UOM test",
			}
		).insert()
		reversal.submit()
		self.assertEqual(self.get_stock(), Decimal("0"))
		self.assertEqual(
			Decimal(
				str(
					frappe.db.get_value(
						"Stock Movement",
						{"source_purchase_reversal": reversal.name},
						"quantity",
					)
				)
			),
			Decimal("120"),
		)

	def test_alternate_uom_does_not_bypass_tenant_product_validation(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_purchase("1", "BOX", product=self.other_product.name)
