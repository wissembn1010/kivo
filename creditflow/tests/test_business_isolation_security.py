from copy import deepcopy
from uuid import uuid4

import frappe
from frappe import client
from frappe.tests import IntegrationTestCase

from creditflow.creditflow.report.customer_balances.customer_balances import execute as customer_balances
from creditflow.creditflow.report.customer_statement.customer_statement import execute as customer_statement
from creditflow.creditflow.report.product_performance.product_performance import execute as product_performance
from creditflow.creditflow.report.purchases_report.purchases_report import execute as purchases_report
from creditflow.creditflow.report.sales_report.sales_report import execute as sales_report
from creditflow.creditflow.report.stock_on_hand.stock_on_hand import execute as stock_on_hand
from creditflow.creditflow.report.supplier_balances.supplier_balances import execute as supplier_balances
from creditflow.creditflow.report.supplier_statement.supplier_statement import execute as supplier_statement


TENANT_DOCTYPES = (
	"Business", "Customer", "Supplier", "Product", "Sale", "Payment", "Purchase",
	"Supplier Payment", "Credit Transaction", "Supplier Transaction", "Stock Movement",
	"Sale Return", "Sale Reversal", "Payment Reversal", "Purchase Reversal",
	"Supplier Payment Reversal",
)


class IntegrationTestBusinessIsolationSecurity(IntegrationTestCase):
	"""Adversarial checks against remotely reachable Frappe permission boundaries."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.token = uuid4().hex[:10]
		self.a = self._make_tenant("A")
		self.b = self._make_tenant("B")
		self.owner_a = self._make_user("owner-a", self.a.business, "OWNER")
		self.staff_a = self._make_user("staff-a", self.a.business, "STAFF")
		self.owner_b = self._make_user("owner-b", self.b.business, "OWNER")
		self._post_activity(self.a, 111, 31, 77, 17)
		self._post_activity(self.b, 222, 42, 133, 23)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _make_user(self, prefix, business, role):
		user = frappe.get_doc({
			"doctype": "User", "email": f"sec-{prefix}-{self.token}@example.com",
			"first_name": prefix, "send_welcome_email": 0, "creditflow_business": business,
		}).insert(ignore_permissions=True)
		user.add_roles(role)
		return user.name

	def _make_tenant(self, marker):
		business = frappe.get_doc({"doctype": "Business", "business_name": f"Security {marker} {self.token}"}).insert()
		customer = frappe.get_doc({"doctype": "Customer", "business": business.name, "customer_name": f"Customer {marker} {self.token}", "credit_limit": 9999}).insert()
		supplier = frappe.get_doc({"doctype": "Supplier", "business": business.name, "supplier_name": f"Supplier {marker} {self.token}"}).insert()
		product = frappe.get_doc({"doctype": "Product", "business": business.name, "product_name": f"Product {marker} {self.token}", "reference": f"SEC-{marker}-{self.token}", "primary_unit": "UNIT", "selling_price": 50}).insert()
		tenant = frappe._dict(business=business.name, customer=customer.name, supplier=supplier.name, product=product.name)
		# Permission probes need stable known IDs; these drafts have no economic side effects.
		for doctype in ("Sale Return", "Sale Reversal", "Payment Reversal", "Purchase Reversal", "Supplier Payment Reversal"):
			doc = frappe.new_doc(doctype)
			doc.name = f"SEC-{marker}-{frappe.scrub(doctype).upper()}-{self.token}"
			doc.business = business.name
			doc.db_insert()
			tenant[frappe.scrub(doctype)] = doc.name
		return tenant

	def _post_activity(self, tenant, sale_total, payment_amount, purchase_total, supplier_payment_amount):
		frappe.get_doc({"doctype": "Stock Movement", "business": tenant.business, "product": tenant.product, "direction": "IN", "quantity": 10, "movement_reason": "OPENING_STOCK", "business_date": frappe.utils.today()}).insert().submit()
		sale = frappe.get_doc({"doctype": "Sale", "business": tenant.business, "customer": tenant.customer, "business_date": frappe.utils.today(), "sale_mode": "CREDIT", "amount_paid": 0, "items": [{"product": tenant.product, "quantity": 1, "unit_price": sale_total}]}).insert(); sale.submit()
		payment = frappe.get_doc({"doctype": "Payment", "business": tenant.business, "customer": tenant.customer, "business_date": frappe.utils.today(), "amount": payment_amount, "payment_method": "CASH"}).insert(); payment.submit()
		purchase = frappe.get_doc({"doctype": "Purchase", "business": tenant.business, "supplier": tenant.supplier, "business_date": frappe.utils.today(), "payment_status": "CREDIT", "items": [{"product": tenant.product, "quantity": 1, "unit_cost": purchase_total}]}).insert(); purchase.submit()
		supplier_payment = frappe.get_doc({"doctype": "Supplier Payment", "business": tenant.business, "supplier": tenant.supplier, "business_date": frappe.utils.today(), "amount": supplier_payment_amount, "payment_method": "CASH"}).insert(); supplier_payment.submit()
		tenant.update(sale=sale.name, payment=payment.name, purchase=purchase.name, supplier_payment=supplier_payment.name)
		for dt, filters, key in (
			("Credit Transaction", {"source_sale": sale.name}, "credit_transaction"),
			("Supplier Transaction", {"source_purchase": purchase.name}, "supplier_transaction"),
			("Stock Movement", {"source_sale": sale.name}, "stock_movement"),
		): tenant[key] = frappe.db.get_value(dt, filters, "name")

	def test_01_known_id_document_and_print_access_is_denied(self):
		frappe.set_user(self.owner_a)
		for doctype, name in ((dt, self.b.get(frappe.scrub(dt))) for dt in TENANT_DOCTYPES):
			with self.assertRaises(frappe.PermissionError, msg=doctype): client.get(doctype, name)
			with self.assertRaises(frappe.PermissionError, msg=f"print {doctype}"):
				from frappe.www.printview import get_html_and_style
				get_html_and_style(doctype, name)

	def test_02_list_rest_filters_and_or_filters_cannot_disclose_other_tenant(self):
		frappe.set_user(self.owner_a)
		for doctype in TENANT_DOCTYPES:
			own = self.a.business if doctype == "Business" else self.a.get(frappe.scrub(doctype))
			other = self.b.business if doctype == "Business" else self.b.get(frappe.scrub(doctype))
			for rows in (
				client.get_list(doctype, fields=["name"], limit_page_length=500),
				client.get_list(doctype, fields=["name"], filters={"name": other}, limit_page_length=500),
				client.get_list(doctype, fields=["name"], or_filters=[["name", "=", other]], limit_page_length=500),
			): self.assertNotIn(other, {r.name for r in rows}, doctype)
			self.assertIn(own, set(frappe.get_list(doctype, pluck="name")), doctype)

	def test_03_business_and_relationship_tampering_has_no_side_effects(self):
		before = self._tenant_snapshot(self.b)
		frappe.set_user(self.owner_a)
		attempts = (
			{"doctype": "Customer", "business": self.b.business, "customer_name": "forged"},
			{"doctype": "Supplier", "business": self.b.business, "supplier_name": "forged"},
			{"doctype": "Product", "business": self.b.business, "product_name": "forged", "reference": f"FORGED-{self.token}"},
			{"doctype": "Sale", "business": self.a.business, "customer": self.b.customer, "business_date": frappe.utils.today(), "sale_mode": "CREDIT", "items": [{"product": self.a.product, "quantity": 1, "unit_price": 1}]},
			{"doctype": "Sale", "business": self.a.business, "customer": self.a.customer, "business_date": frappe.utils.today(), "sale_mode": "CREDIT", "items": [{"product": self.b.product, "quantity": 1, "unit_price": 1}]},
			{"doctype": "Payment", "business": self.a.business, "customer": self.b.customer, "business_date": frappe.utils.today(), "amount": 1},
			{"doctype": "Purchase", "business": self.a.business, "supplier": self.b.supplier, "business_date": frappe.utils.today(), "items": [{"product": self.a.product, "quantity": 1, "unit_cost": 1}]},
			{"doctype": "Purchase", "business": self.a.business, "supplier": self.a.supplier, "business_date": frappe.utils.today(), "items": [{"product": self.b.product, "quantity": 1, "unit_cost": 1}]},
			{"doctype": "Supplier Payment", "business": self.a.business, "supplier": self.b.supplier, "business_date": frappe.utils.today(), "amount": 1},
			{"doctype": "Stock Movement", "business": self.a.business, "product": self.b.product, "direction": "IN", "quantity": 1, "movement_reason": "MANUAL_ADJUSTMENT", "business_date": frappe.utils.today()},
		)
		for values in attempts:
			with self.assertRaises((frappe.PermissionError, frappe.ValidationError), msg=values["doctype"]): frappe.get_doc(values).insert()
		frappe.set_user("Administrator")
		self.assertEqual(before, self._tenant_snapshot(self.b))

	def test_04_update_delete_cancel_and_cross_business_reversal_are_denied(self):
		before = self._tenant_snapshot(self.b)
		frappe.set_user(self.owner_a)
		for doctype, name, field, value in (
			("Customer", self.b.customer, "notes", "stolen"), ("Supplier", self.b.supplier, "notes", "stolen"),
			("Product", self.b.product, "selling_price", 1), ("Sale", self.b.sale, "business_date", frappe.utils.add_days(frappe.utils.today(), -1)),
			("Purchase", self.b.purchase, "business_date", frappe.utils.add_days(frappe.utils.today(), -1)),
		):
			doc = frappe.get_doc(doctype, name); doc.set(field, value)
			with self.assertRaises(frappe.PermissionError, msg=doctype): doc.save()
		for doctype, name in (("Payment", self.b.payment), ("Supplier Payment", self.b.supplier_payment)):
			with self.assertRaises(frappe.PermissionError): client.cancel(doctype, name)
		with self.assertRaises(frappe.PermissionError): client.delete("Customer", self.b.customer)
		for doctype, original_field, original in (("Sale Reversal", "original_sale", self.b.sale), ("Purchase Reversal", "original_purchase", self.b.purchase), ("Payment Reversal", "original_payment", self.b.payment), ("Supplier Payment Reversal", "original_supplier_payment", self.b.supplier_payment)):
			with self.assertRaises((frappe.PermissionError, frappe.ValidationError)): frappe.get_doc({"doctype": doctype, "business": self.a.business, original_field: original, "business_date": frappe.utils.today(), "reason": "attack"}).insert()
		frappe.set_user("Administrator")
		self.assertEqual(before, self._tenant_snapshot(self.b))

	def test_05_report_parameter_tampering_and_aggregate_isolation(self):
		frappe.set_user(self.owner_a)
		for execute, filters in (
			(customer_balances, {"business": self.b.business, "show_zero_balances": 1}),
			(customer_statement, {"business": self.b.business, "customer": self.b.customer}),
			(sales_report, {"business": self.b.business}), (supplier_balances, {"business": self.b.business, "show_zero_balances": 1}),
			(supplier_statement, {"business": self.b.business, "supplier": self.b.supplier}),
			(purchases_report, {"business": self.b.business}), (stock_on_hand, {"business": self.b.business, "show_zero_stock": 1}),
			(product_performance, {"business": self.b.business}),
		):
			with self.assertRaises(frappe.PermissionError): execute(filters)
		for execute, foreign_field in ((customer_balances, "customer"), (sales_report, "sale"), (supplier_balances, "supplier"), (purchases_report, "purchase"), (stock_on_hand, "product"), (product_performance, "product")):
			rows = execute({"show_zero_balances": 1, "show_zero_stock": 1})[1]
			self.assertNotIn(self.b.get(foreign_field), {r.get(foreign_field) for r in rows})

	def test_06_dashboard_whitelisted_aggregates_are_tenant_scoped(self):
		from creditflow import dashboard
		frappe.set_user(self.owner_a)
		customer_debt = dashboard.total_customer_debt()["value"]
		supplier_debt = dashboard.total_supplier_debt()["value"]
		frappe.set_user("Administrator")
		self.assertEqual(str(customer_debt), frappe.get_doc("Customer", self.a.customer).get_ledger_balance())
		self.assertEqual(str(supplier_debt), frappe.get_doc("Supplier", self.a.supplier).get_ledger_balance())

	def test_07_staff_cannot_mutate_ledgers_masters_reversals_or_import(self):
		from creditflow.onboarding import preview_csv
		frappe.set_user(self.staff_a)
		for doctype in ("Customer", "Supplier", "Product", "Credit Transaction", "Supplier Transaction", "Stock Movement", "Sale Reversal", "Purchase Reversal"):
			self.assertFalse(frappe.has_permission(doctype, "create"), doctype)
		with self.assertRaises(frappe.PermissionError): preview_csv("CUSTOMERS", "customer_name\nforged\n", self.a.business)

	def _tenant_snapshot(self, tenant):
		return deepcopy({
			"customer_balance": frappe.get_doc("Customer", tenant.customer).get_ledger_balance(),
			"supplier_balance": frappe.get_doc("Supplier", tenant.supplier).get_ledger_balance(),
			"product": frappe.db.get_value("Product", tenant.product, ["selling_price", "modified"], as_dict=True),
			"counts": {dt: frappe.db.count(dt, {"business": tenant.business}) for dt in TENANT_DOCTYPES if dt != "Business"},
		})
