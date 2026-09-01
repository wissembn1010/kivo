from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.creditflow.report.supplier_statement.supplier_statement import execute


class IntegrationTestSupplierStatement(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Supplier Statement Business {suffix}"}
		).insert()
		self.supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": self.business.name,
				"supplier_name": f"Statement Supplier {suffix}",
			}
		).insert()
		self.product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business.name,
				"product_name": f"Statement Product {suffix}",
				"reference": f"STATEMENT-{suffix}",
				"primary_unit": "UNIT",
			}
		).insert()

	def make_purchase(self, amount):
		purchase = frappe.get_doc(
			{
				"doctype": "Purchase",
				"business": self.business.name,
				"supplier": self.supplier.name,
				"business_date": frappe.utils.today(),
				"items": [
					{
						"product": self.product.name,
						"quantity": "1",
						"unit_cost": amount,
					}
				],
			}
		).insert()
		purchase.submit()
		return purchase

	def test_total_row_uses_final_running_balance_for_multiple_ledger_rows(self):
		self.make_purchase("100")
		self.make_purchase("20")

		_, rows, _, _, _, skip_total_row = execute({"supplier": self.supplier.name})

		ledger_rows = rows[:-1]
		total = rows[-1]
		self.assertEqual([row.running_balance for row in ledger_rows], [Decimal("100.000"), Decimal("120.000")])
		self.assertEqual(total.debit, Decimal("120.000"))
		self.assertEqual(total.credit, Decimal("0.000"))
		self.assertEqual(total.running_balance, Decimal("120.000"))
		self.assertTrue(skip_total_row)
