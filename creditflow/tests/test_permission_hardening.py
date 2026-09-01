from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestPermissionHardening(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.suffix = uuid4().hex[:10]
		self.business_a = self.make_business(f"Security A {self.suffix}")
		self.business_b = self.make_business(f"Security B {self.suffix}")
		self.owner_a = self.make_user("owner-a", self.business_a.name, "OWNER")
		self.staff_a = self.make_user("staff-a", self.business_a.name, "STAFF")
		self.owner_b = self.make_user("owner-b", self.business_b.name, "OWNER")
		self.staff_b = self.make_user("staff-b", self.business_b.name, "STAFF")
		self.customer_a = self.make_customer(self.business_a.name, "Customer A")
		self.customer_b = self.make_customer(self.business_b.name, "Customer B")
		self.supplier_a = self.make_supplier(self.business_a.name, "Supplier A")
		self.supplier_b = self.make_supplier(self.business_b.name, "Supplier B")
		self.product_a = self.make_product(self.business_a.name, "PRODUCT-A")
		self.product_b = self.make_product(self.business_b.name, "PRODUCT-B")

	def tearDown(self):
		frappe.set_user("Administrator")

	def make_business(self, name):
		return frappe.get_doc({"doctype": "Business", "business_name": name}).insert()

	def make_user(self, prefix, business, role):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"security-{prefix}-{self.suffix}@example.com",
				"first_name": prefix,
				"send_welcome_email": 0,
				"creditflow_business": business,
			}
		).insert(ignore_permissions=True)
		user.add_roles(role)
		return user.name

	def make_customer(self, business, label):
		return frappe.get_doc(
			{
				"doctype": "Customer",
				"business": business,
				"customer_name": f"{label} {self.suffix}",
			}
		).insert()

	def make_supplier(self, business, label):
		return frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": business,
				"supplier_name": f"{label} {self.suffix}",
			}
		).insert()

	def make_product(self, business, label):
		return frappe.get_doc(
			{
				"doctype": "Product",
				"business": business,
				"product_name": f"{label} {self.suffix}",
				"reference": f"{label}-{self.suffix}",
				"primary_unit": "UNIT",
			}
		).insert()

	def add_stock(self, quantity="10"):
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business_a.name,
				"product": self.product_a.name,
				"direction": "IN",
				"quantity": quantity,
				"movement_reason": "OPENING_STOCK",
				"business_date": frappe.utils.today(),
			}
		).insert().submit()

	def sale(self, business=None, customer=None, product=None):
		return frappe.get_doc(
			{
				"doctype": "Sale",
				"business": business or self.business_a.name,
				"customer": customer or self.customer_a.name,
				"business_date": frappe.utils.today(),
				"sale_mode": "CREDIT",
				"amount_paid": 0,
				"items": [
					{
						"product": product or self.product_a.name,
						"quantity": 1,
						"unit_price": 5,
					}
				],
			}
		)

	def test_staff_cannot_directly_create_economic_ledgers(self):
		frappe.set_user(self.staff_a)
		attempts = (
			{
				"doctype": "Credit Transaction",
				"business": self.business_a.name,
				"customer": self.customer_a.name,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": 1,
				"transaction_date": frappe.utils.today(),
			},
			{
				"doctype": "Supplier Transaction",
				"business": self.business_a.name,
				"supplier": self.supplier_a.name,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": 1,
				"transaction_date": frappe.utils.today(),
			},
			{
				"doctype": "Stock Movement",
				"business": self.business_a.name,
				"product": self.product_a.name,
				"direction": "IN",
				"quantity": 1,
				"movement_reason": "MANUAL_ADJUSTMENT",
				"business_date": frappe.utils.today(),
			},
		)
		for values in attempts:
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc(values).insert()

	def test_staff_sale_still_generates_trusted_ledger_and_stock_records(self):
		self.add_stock()
		frappe.set_user(self.staff_a)
		sale = self.sale().insert()
		sale.submit()
		self.assertEqual(
			frappe.db.count("Credit Transaction", {"source_sale": sale.name, "docstatus": 1}),
			1,
		)
		self.assertEqual(
			frappe.db.count("Stock Movement", {"source_sale": sale.name, "docstatus": 1}),
			1,
		)

	def test_staff_payment_still_generates_trusted_credit_transaction(self):
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Credit Transaction",
				"business": self.business_a.name,
				"customer": self.customer_a.name,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": 10,
				"transaction_date": frappe.utils.today(),
			}
		).insert().submit()
		frappe.set_user(self.staff_a)
		payment = frappe.get_doc(
			{
				"doctype": "Payment",
				"business": self.business_a.name,
				"customer": self.customer_a.name,
				"amount": 2,
				"business_date": frappe.utils.today(),
			}
		).insert()
		payment.submit()
		self.assertEqual(
			frappe.db.count(
				"Credit Transaction", {"source_payment": payment.name, "docstatus": 1}
			),
			1,
		)

	def test_owner_retains_master_operational_ledger_and_reversal_permissions(self):
		frappe.set_user(self.owner_a)
		for doctype, ptype in (
			("Customer", "create"),
			("Supplier", "create"),
			("Product", "create"),
			("Sale", "create"),
			("Purchase", "create"),
			("Payment", "create"),
			("Supplier Payment", "create"),
			("Credit Transaction", "create"),
			("Supplier Transaction", "create"),
			("Stock Movement", "create"),
			("Sale Reversal", "create"),
			("Payment Reversal", "create"),
			("Purchase Reversal", "create"),
			("Supplier Payment Reversal", "create"),
		):
			self.assertTrue(frappe.has_permission(doctype, ptype), (doctype, ptype))

	def test_owner_and_staff_cannot_create_or_modify_cross_business_records(self):
		for user in (self.owner_a, self.staff_a):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				self.sale(
					business=self.business_b.name,
					customer=self.customer_b.name,
					product=self.product_b.name,
				).insert()
			other_customer = frappe.get_doc("Customer", self.customer_b.name)
			other_customer.notes = "forged"
			with self.assertRaises(frappe.PermissionError):
				other_customer.save()

	def test_owner_and_staff_cannot_reverse_cross_business_sale(self):
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business_b.name,
				"product": self.product_b.name,
				"direction": "IN",
				"quantity": 2,
				"movement_reason": "OPENING_STOCK",
				"business_date": frappe.utils.today(),
			}
		).insert().submit()
		sale_b = self.sale(
			business=self.business_b.name,
			customer=self.customer_b.name,
			product=self.product_b.name,
		).insert()
		sale_b.submit()

		frappe.set_user(self.owner_a)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Sale Reversal",
					"business": self.business_a.name,
					"original_sale": sale_b.name,
					"business_date": frappe.utils.today(),
					"reason": "forged",
				}
			).insert()

		frappe.set_user(self.staff_a)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Sale Reversal",
					"business": self.business_a.name,
					"original_sale": sale_b.name,
					"business_date": frappe.utils.today(),
					"reason": "forged",
				}
			).insert()

	def test_system_source_and_reversal_fields_cannot_be_forged(self):
		frappe.set_user(self.owner_a)
		for values in (
			{
				"doctype": "Credit Transaction",
				"business": self.business_a.name,
				"customer": self.customer_a.name,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": 1,
				"transaction_date": frappe.utils.today(),
				"source_doctype": "Sale",
			},
			{
				"doctype": "Supplier Transaction",
				"business": self.business_a.name,
				"supplier": self.supplier_a.name,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": 1,
				"transaction_date": frappe.utils.today(),
				"source_purchase": "forged",
			},
			{
				"doctype": "Stock Movement",
				"business": self.business_a.name,
				"product": self.product_a.name,
				"direction": "IN",
				"quantity": 1,
				"movement_reason": "MANUAL_ADJUSTMENT",
				"business_date": frappe.utils.today(),
				"source_sale": "forged",
			},
		):
			with self.assertRaises(frappe.ValidationError):
				frappe.get_doc(values).insert()

	def test_reversal_permissions_are_owner_only(self):
		for doctype in (
			"Sale Reversal",
			"Payment Reversal",
			"Purchase Reversal",
			"Supplier Payment Reversal",
		):
			self.assertFalse(frappe.has_permission(doctype, "create", user=self.staff_a))
			self.assertTrue(frappe.has_permission(doctype, "create", user=self.owner_a))

	def test_staff_master_data_restrictions_are_preserved(self):
		for doctype in ("Customer", "Supplier", "Product"):
			self.assertFalse(frappe.has_permission(doctype, "create", user=self.staff_a))
			self.assertFalse(frappe.has_permission(doctype, "write", user=self.staff_a))
			self.assertTrue(frappe.has_permission(doctype, "read", user=self.staff_a))
