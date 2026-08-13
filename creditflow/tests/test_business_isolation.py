from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


PROTECTED_DOCTYPES = (
	"Business",
	"Customer",
	"Product",
	"Supplier",
	"Sale",
	"Payment",
	"Purchase",
	"Sale Reversal",
	"Payment Reversal",
	"Purchase Reversal",
	"Credit Transaction",
	"Stock Movement",
)


class IntegrationTestBusinessIsolation(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:10]
		self.business_a = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Isolation A {suffix}"}
		).insert()
		self.business_b = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Isolation B {suffix}"}
		).insert()
		self.user_a = self.make_user(f"isolation-a-{suffix}@example.com", self.business_a.name, "STAFF")
		self.user_b = self.make_user(f"isolation-b-{suffix}@example.com", self.business_b.name, "OWNER")
		self.system_manager = self.make_user(
			f"isolation-admin-{suffix}@example.com", None, "System Manager"
		)
		self.records_a = self.make_business_records(self.business_a, suffix, "A")
		self.records_b = self.make_business_records(self.business_b, suffix, "B")

	def tearDown(self):
		frappe.set_user("Administrator")

	def make_user(self, email, business, role):
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

	def make_business_records(self, business, suffix, marker):
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"business": business.name,
				"customer_name": f"Isolation Customer {marker} {suffix}",
			}
		).insert()
		product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": business.name,
				"product_name": f"Isolation Product {marker} {suffix}",
				"reference": f"ISO-{marker}-{suffix}",
			}
		).insert()
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": business.name,
				"supplier_name": f"Isolation Supplier {marker} {suffix}",
			}
		).insert()

		records = {
			"Business": business.name,
			"Customer": customer.name,
			"Product": product.name,
			"Supplier": supplier.name,
		}
		for doctype in PROTECTED_DOCTYPES[4:]:
			doc = frappe.new_doc(doctype)
			doc.name = f"ISOLATION-{marker}-{doctype.upper().replace(' ', '-')}-{suffix}"
			doc.business = business.name
			doc.db_insert()
			records[doctype] = doc.name
		return records

	def assert_only_assigned_business_is_visible(self, user, own_records, other_records):
		frappe.set_user(user)
		for doctype in PROTECTED_DOCTYPES:
			if (
				doctype in ("Sale Reversal", "Payment Reversal", "Purchase Reversal")
				and "OWNER" not in frappe.get_roles(user)
			):
				with self.assertRaises(frappe.PermissionError):
					frappe.get_list(doctype, pluck="name")
				continue
			names = frappe.get_list(doctype, pluck="name")
			self.assertIn(own_records[doctype], names, doctype)
			self.assertNotIn(other_records[doctype], names, doctype)

	def test_users_only_list_their_assigned_business_records(self):
		self.assert_only_assigned_business_is_visible(
			self.user_a, self.records_a, self.records_b
		)
		self.assert_only_assigned_business_is_visible(
			self.user_b, self.records_b, self.records_a
		)

	def test_direct_access_to_another_business_is_denied(self):
		frappe.set_user(self.user_a)
		for doctype in PROTECTED_DOCTYPES:
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc(doctype, self.records_b[doctype]).check_permission("read")

	def test_cross_business_operational_creation_is_denied(self):
		frappe.set_user(self.user_a)
		for doctype in ("Sale", "Payment", "Purchase"):
			doc = frappe.new_doc(doctype)
			doc.business = self.business_b.name
			with self.assertRaises(frappe.PermissionError):
				doc.insert()

	def test_business_tampering_is_denied(self):
		frappe.set_user(self.user_a)
		customer = frappe.get_doc("Customer", self.records_a["Customer"])
		customer.business = self.business_b.name
		with self.assertRaises(frappe.PermissionError):
			customer.save()

	def test_operational_business_is_defaulted_server_side(self):
		frappe.set_user(self.user_a)
		sale = frappe.new_doc("Sale")
		from creditflow.permissions import set_and_validate_business

		set_and_validate_business(sale)
		self.assertEqual(sale.business, self.business_a.name)

	def test_administrators_have_cross_business_access(self):
		for user in ("Administrator", self.system_manager):
			frappe.set_user(user)
			for doctype in PROTECTED_DOCTYPES:
				names = frappe.get_list(doctype, pluck="name")
				self.assertIn(self.records_a[doctype], names, (user, doctype))
				self.assertIn(self.records_b[doctype], names, (user, doctype))
