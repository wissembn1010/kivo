from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from creditflow import dashboard


class IntegrationTestWorkspaceDashboard(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:10]
		self.business_a = self._make_business(f"Dashboard A {suffix}")
		self.business_b = self._make_business(f"Dashboard B {suffix}")
		self.staff = self._make_user(
			f"dashboard-staff-{suffix}@example.com", self.business_a, "STAFF"
		)
		self.owner = self._make_user(
			f"dashboard-owner-{suffix}@example.com", self.business_b, "OWNER"
		)
		self.admin_baseline = {
			"debt": dashboard.total_customer_debt()["value"],
			"sales": dashboard.sales_today()["value"],
			"payments": dashboard.payments_today()["value"],
			"purchases": dashboard.purchases_today()["value"],
			"negative_stock": dashboard.negative_stock_products()["value"],
		}
		self._make_kpi_records(self.business_a, suffix, "A", Decimal("12.345"))
		self._make_kpi_records(self.business_b, suffix, "B", Decimal("100.000"))

	def tearDown(self):
		frappe.set_user("Administrator")

	def _make_business(self, business_name):
		return frappe.get_doc(
			{"doctype": "Business", "business_name": business_name}
		).insert().name

	def _make_user(self, email, business, role):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"creditflow_business": business,
			}
		).insert(ignore_permissions=True)
		user.add_roles(role)
		return user.name

	def _make_kpi_records(self, business, suffix, marker, scale):
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"business": business,
				"customer_name": f"Dashboard Customer {marker} {suffix}",
			}
		).insert()
		product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": business,
				"product_name": f"Dashboard Product {marker} {suffix}",
				"reference": f"DASH-{marker}-{suffix}",
			}
		).insert()

		for direction, amount in (("DEBIT", scale), ("CREDIT", Decimal("2.000"))):
			doc = frappe.new_doc("Credit Transaction")
			doc.update(
				{
					"name": f"DASH-{marker}-{direction}-{suffix}",
					"business": business,
					"customer": customer.name,
					"transaction_type": "OPENING_BALANCE",
					"direction": direction,
					"amount": amount,
					"transaction_date": today(),
					"docstatus": 1,
				}
			)
			doc.db_insert()

		for doctype, amount_field, amount in (
			("Sale", "total_amount", scale),
			("Payment", "amount", Decimal("3.210")),
			("Purchase", "total_amount", Decimal("9.876")),
		):
			doc = frappe.new_doc(doctype)
			doc.update(
				{
					"name": f"DASH-{marker}-{doctype.upper()}-{suffix}",
					"business": business,
					"business_date": today(),
					amount_field: amount,
					"docstatus": 1,
				}
			)
			doc.db_insert()

		movement = frappe.new_doc("Stock Movement")
		movement.update(
			{
				"name": f"DASH-{marker}-STOCK-{suffix}",
				"business": business,
				"product": product.name,
				"direction": "OUT",
				"quantity": 1,
				"movement_reason": "MANUAL_ADJUSTMENT",
				"business_date": today(),
				"docstatus": 1,
			}
		)
		movement.db_insert()

	def test_workspace_metadata_and_targets_are_installed(self):
		workspace = frappe.get_doc("Workspace", "CreditFlow")
		self.assertEqual(workspace.module, "CreditFlow")
		self.assertEqual({row.role for row in workspace.roles}, {"OWNER", "STAFF"})
		self.assertEqual(
			{row.label for row in workspace.shortcuts},
			{"New Sale", "New Payment", "New Purchase"},
		)

		for row in workspace.links:
			if row.type != "Link":
				continue
			if row.link_type == "DocType":
				self.assertTrue(frappe.db.exists("DocType", row.link_to), row.link_to)
			elif row.link_type == "Report":
				self.assertTrue(frappe.db.exists("Report", row.link_to), row.link_to)

		for card_name in (
			"Total Customer Debt",
			"Sales Today",
			"Payments Today",
			"Purchases Today",
			"Negative Stock Products",
		):
			self.assertTrue(frappe.db.exists("Number Card", card_name), card_name)

	def test_staff_kpis_are_limited_to_assigned_business(self):
		frappe.set_user(self.staff)
		self.assertEqual(dashboard.total_customer_debt()["value"], Decimal("10.345"))
		self.assertEqual(dashboard.sales_today()["value"], Decimal("12.345"))
		self.assertEqual(dashboard.payments_today()["value"], Decimal("3.210"))
		self.assertEqual(dashboard.purchases_today()["value"], Decimal("9.876"))
		self.assertEqual(dashboard.negative_stock_products()["value"], 1)

	def test_administrator_kpis_have_cross_business_access(self):
		frappe.set_user("Administrator")
		self.assertEqual(
			dashboard.total_customer_debt()["value"],
			self.admin_baseline["debt"] + Decimal("108.345"),
		)
		self.assertEqual(
			dashboard.sales_today()["value"],
			self.admin_baseline["sales"] + Decimal("112.345"),
		)
		self.assertEqual(
			dashboard.payments_today()["value"],
			self.admin_baseline["payments"] + Decimal("6.420"),
		)
		self.assertEqual(
			dashboard.purchases_today()["value"],
			self.admin_baseline["purchases"] + Decimal("19.752"),
		)
		self.assertEqual(
			dashboard.negative_stock_products()["value"],
			self.admin_baseline["negative_stock"] + 2,
		)

	def test_workspace_does_not_grant_staff_reversal_permissions(self):
		for doctype in ("Sale Reversal", "Payment Reversal", "Purchase Reversal"):
			self.assertFalse(frappe.has_permission(doctype, "read", user=self.staff))
			self.assertFalse(frappe.has_permission(doctype, "create", user=self.staff))
			self.assertTrue(frappe.has_permission(doctype, "read", user=self.owner))
			self.assertTrue(frappe.has_permission(doctype, "create", user=self.owner))
