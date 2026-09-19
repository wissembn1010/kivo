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
			"cash_collected": dashboard.cash_collected_today()["value"],
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
			if doctype == "Sale":
				doc.update(
					{
						"sale_mode": "CASH",
						"amount_paid": amount,
						"outstanding_amount": 0,
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
		workspace = frappe.get_doc("Workspace", "Kivo")
		self.assertEqual(workspace.module, "CreditFlow")
		self.assertEqual(workspace.title, "Kivo")
		self.assertEqual(workspace.app, "creditflow")
		self.assertEqual({row.role for row in workspace.roles}, {"OWNER", "STAFF"})
		self.assertEqual(
			{row.label for row in workspace.shortcuts},
			{
				"New Sale",
				"New Customer Payment",
				"New Purchase",
				"New Customer",
				"New Supplier",
				"New Product",
			},
		)
		self.assertEqual(
			{row.label for row in workspace.links if row.type == "Card Break"},
			{"Sales", "Purchases", "Inventory", "Fiscal", "Reports", "Settings"},
		)

		for row in workspace.links:
			if row.type != "Link":
				continue
			if row.link_type == "DocType":
				self.assertTrue(frappe.db.exists("DocType", row.link_to), row.link_to)
			elif row.link_type == "Report":
				self.assertTrue(frappe.db.exists("Report", row.link_to), row.link_to)
			elif row.link_type == "Page":
				self.assertTrue(frappe.db.exists("Page", row.link_to), row.link_to)

		for card_name in (
			"Total Customer Debt",
			"Net Sales Today",
			"Cash Collected Today",
			"Net Sales This Month",
			"Purchases This Month",
			"Low / Out of Stock Products",
		):
			self.assertTrue(frappe.db.exists("Number Card", card_name), card_name)

	def test_kivo_sidebar_uses_business_workflows_and_real_targets(self):
		sidebar = frappe.get_doc("Workspace Sidebar", "Kivo")
		self.assertEqual(sidebar.app, "creditflow")
		self.assertEqual(sidebar.module, "CreditFlow")
		self.assertEqual(
			{row.label for row in sidebar.items if row.type == "Section Break"},
			{"Sales", "Purchases", "Inventory", "Fiscal", "Reports", "Settings"},
		)
		for row in sidebar.items:
			if row.type != "Link" or row.link_type == "URL":
				continue
			self.assertTrue(frappe.db.exists(row.link_type, row.link_to), row.link_to)

		url_items = {row.label: row.url for row in sidebar.items if row.link_type == "URL"}
		self.assertEqual(url_items, {"Language": "/kivo-language", "Subscription & Billing": "/creditflow-subscription"})

	def test_primary_navigation_hides_internal_models(self):
		technical = {
			"Credit Transaction",
			"Supplier Transaction",
			"TEJ Operation Code",
			"TEJ Country Code",
			"CreditFlow Pending Signup",
			"CreditFlow Plan Entitlement",
			"CreditFlow Subscription",
			"CreditFlow Billing Payment",
			"CreditFlow Plan",
		}
		workspace_targets = {
			row.link_to for row in frappe.get_doc("Workspace", "Kivo").links if row.type == "Link"
		}
		sidebar_targets = {
			row.link_to for row in frappe.get_doc("Workspace Sidebar", "Kivo").items if row.type == "Link"
		}
		self.assertFalse(technical & workspace_targets)
		self.assertFalse(technical & sidebar_targets)

	def test_owner_can_see_kivo_workspace_and_fiscal_destinations(self):
		from frappe.desk.desktop import get_workspaces

		frappe.set_user(self.owner)
		workspace_names = {row.name for row in get_workspaces()["pages"]}
		self.assertIn("Kivo", workspace_names)
		self.assertTrue(frappe.has_permission("Withholding Tax", "read"))
		self.assertTrue(frappe.has_permission("TEJ Export Batch", "read"))

	def test_owner_boot_payload_uses_live_kivo_sidebar(self):
		from frappe.boot import get_bootinfo

		frappe.set_user(self.owner)
		bootinfo = get_bootinfo()
		self.assertIn("kivo", bootinfo.workspace_sidebar_item)
		sidebar = bootinfo.workspace_sidebar_item["kivo"]
		self.assertEqual(sidebar["label"], "Kivo")
		self.assertEqual(sidebar["app"], "creditflow")
		self.assertEqual(
			[item["label"] for item in sidebar["items"] if not item["child"]],
			["Dashboard", "Sales", "Purchases", "Inventory", "Fiscal", "Reports", "Settings"],
		)
		self.assertNotIn("Credit Transaction", {item["link_to"] for item in sidebar["items"]})


	def test_owner_can_load_every_sidebar_report_through_desk_api(self):
		from frappe.desk import query_report

		reports = (
			"Sales Report",
			"Purchases Report",
			"Customer Balances",
			"Supplier Balances",
			"Customer Statement",
			"Supplier Statement",
			"Product Performance",
			"Stock On Hand",
		)
		frappe.set_user(self.owner)
		for report_name in reports:
			script = query_report.get_script(report_name)
			self.assertIn("frappe.query_reports", script["script"])
			self.assertIn(report_name, script["script"])

		for report_name in reports:
			reference_doctype = frappe.db.get_value("Report", report_name, "ref_doctype")
			self.assertTrue(frappe.has_permission(reference_doctype, "report"), report_name)

		for report_name in reports:
			if report_name in {"Customer Statement", "Supplier Statement"}:
				continue
			result = query_report.run(report_name, filters={})
			self.assertTrue(result["columns"], report_name)


	def test_staff_kpis_are_limited_to_assigned_business(self):
		frappe.set_user(self.staff)
		self.assertEqual(dashboard.total_customer_debt()["value"], Decimal("10.345"))
		self.assertEqual(dashboard.cash_collected_today()["value"], Decimal("15.555"))
		self.assertEqual(dashboard.sales_today()["value"], Decimal("12.345"))
		self.assertEqual(dashboard.payments_today()["value"], Decimal("3.210"))
		self.assertEqual(dashboard.purchases_today()["value"], Decimal("9.876"))
		self.assertEqual(dashboard.negative_stock_products()["value"], 1)

	def test_negative_stock_excludes_zero_and_nonnegative_low_stock(self):
		from creditflow.creditflow.report.stock_on_hand.stock_on_hand import execute

		negative_product = None
		for stock in (8, 0, 2, 5, -1):
			product = frappe.get_doc({"doctype": "Product", "business": self.business_a,
				"product_name": f"Stock boundary {stock}", "reference": uuid4().hex,
				"minimum_stock": 5}).insert()
			if stock:
				# Historical ledger fixture; ordinary posting still prevents negative stock.
				movement = frappe.get_doc({"doctype": "Stock Movement", "name": uuid4().hex,
					"business": self.business_a, "product": product.name,
					"direction": "IN" if stock > 0 else "OUT", "quantity": abs(stock),
					"movement_reason": "MANUAL_ADJUSTMENT", "business_date": today(), "docstatus": 1})
				movement.db_insert()
			if stock < 0:
				negative_product = product.name
			frappe.set_user(self.staff)
			self.assertEqual(dashboard.negative_stock_products()["value"], 2 if stock < 0 else 1)
			frappe.set_user("Administrator")

		frappe.set_user(self.staff)
		card = dashboard.negative_stock_products({"business": self.business_b})
		self.assertEqual(card["value"], 2)
		self.assertEqual(dashboard.low_stock_products()["value"], 5)
		_, rows = execute(card["route_options"])
		self.assertEqual(len(rows), card["value"])
		self.assertIn(negative_product, {row.product for row in rows})
		self.assertTrue(all(row.business == self.business_a and row.stock_on_hand < 0 for row in rows))
		frappe.set_user(self.owner)
		self.assertEqual(dashboard.negative_stock_products({"business": self.business_a})["value"], 1)

	def test_administrator_kpis_have_cross_business_access(self):
		frappe.set_user("Administrator")
		self.assertEqual(
			dashboard.total_customer_debt()["value"],
			self.admin_baseline["debt"] + Decimal("108.345"),
		)
		self.assertEqual(
			dashboard.cash_collected_today()["value"],
			self.admin_baseline["cash_collected"] + Decimal("118.765"),
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
