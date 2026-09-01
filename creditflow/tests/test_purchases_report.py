from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.creditflow.report.purchases_report.purchases_report import execute


class IntegrationTestPurchasesReport(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Purchases Report Business {suffix}"}
		).insert()
		self.supplier = self.make_supplier(f"Purchases Report Supplier {suffix}")
		self.other_supplier = self.make_supplier(f"Other Purchases Report Supplier {suffix}")
		self.product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business.name,
				"product_name": f"Purchases Report Product {suffix}",
				"reference": f"PUR-REPORT-{suffix}",
				"primary_unit": "UNIT",
			}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")

	def make_supplier(self, supplier_name):
		return frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": self.business.name,
				"supplier_name": supplier_name,
			}
		).insert()

	def make_purchase(self, purchase_date, supplier=None):
		purchase = frappe.get_doc(
			{
				"doctype": "Purchase",
				"business": self.business.name,
				"supplier": supplier or self.supplier.name,
				"business_date": purchase_date,
				"items": [
					{
						"product": self.product.name,
						"quantity": "1",
						"unit_cost": "1",
					}
				],
			}
		).insert()
		purchase.submit()
		return purchase

	def report_purchase_names(self, **filters):
		_, rows = execute({"business": self.business.name, **filters})
		return {row.purchase for row in rows}

	def test_purchase_before_range_is_excluded(self):
		purchase = self.make_purchase("2026-08-17")
		names = self.report_purchase_names(from_date="2026-08-18", to_date="2026-08-31")
		self.assertNotIn(purchase.name, names)

	def test_purchase_inside_range_is_included(self):
		purchase = self.make_purchase("2026-08-18")
		names = self.report_purchase_names(from_date="2026-08-18", to_date="2026-08-31")
		self.assertIn(purchase.name, names)

	def test_purchase_after_range_is_excluded(self):
		purchase = self.make_purchase("2026-09-01")
		names = self.report_purchase_names(from_date="2026-08-18", to_date="2026-08-31")
		self.assertNotIn(purchase.name, names)

	def test_supplier_and_date_filters_are_applied_together(self):
		matching = self.make_purchase("2026-08-20")
		wrong_supplier = self.make_purchase("2026-08-20", self.other_supplier.name)
		outside_range = self.make_purchase("2026-08-17")
		names = self.report_purchase_names(
			from_date="2026-08-18",
			to_date="2026-08-31",
			supplier=self.supplier.name,
		)
		self.assertIn(matching.name, names)
		self.assertNotIn(wrong_supplier.name, names)
		self.assertNotIn(outside_range.name, names)
