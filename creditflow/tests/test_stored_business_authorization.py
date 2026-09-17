from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe import client
from frappe.desk.form.save import savedocs
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from creditflow.permissions import has_permission, set_and_validate_business


def run_in_memory_probe():
	"""Repeat B-28 with the actual bulk endpoint/hooks, without writing any rows."""

	class ProbeDocument(frappe._dict):
		def is_new(self):
			return False

		def save(self):
			if not has_permission(self, "write", "fix001-owner@example.com"):
				raise frappe.PermissionError
			set_and_validate_business(self)
			self.saved = True

	def stored_value(doctype, name, fieldname, **kwargs):
		if doctype == "User":
			return "Business A"
		if doctype == "Customer" and name == "foreign-customer":
			return frappe._dict(business="Business B") if kwargs.get("as_dict") else "Business B"
		raise AssertionError((doctype, name, fieldname))

	doc = ProbeDocument(doctype="Customer", name="foreign-customer", business="Business B")
	with (
		patch.object(frappe, "get_doc", return_value=doc),
		patch.object(frappe, "get_roles", return_value=["OWNER"]),
		patch.object(frappe.db, "get_value", side_effect=stored_value),
		patch("creditflow.permissions.has_cross_business_access", return_value=False),
		patch("creditflow.subscription.require_write_access"),
	):
		result = client.bulk_update(
			frappe.as_json(
				[
					{
						"doctype": "Customer",
						"docname": "foreign-customer",
						"business": "Business A",
					}
				]
			)
		)
	return "BLOCKED" if result["failed_docs"] and not doc.saved else "VULNERABLE"


class IntegrationTestStoredBusinessAuthorization(IntegrationTestCase):
	"""FIX-001: incoming ownership must never authorize a persisted foreign row."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.token = uuid4().hex[:10]
		self.business_a = self.business("A")
		self.business_b = self.business("B")
		self.owner_a = self.user("OWNER", self.business_a)
		self.staff_a = self.user("STAFF", self.business_a)
		self.customer_a = self.customer(self.business_a, "A")
		self.customer_b = self.customer(self.business_b, "B")
		frappe.set_user(self.owner_a)
		self.addCleanup(frappe.set_user, "Administrator")

	def business(self, label):
		return (
			frappe.get_doc({"doctype": "Business", "business_name": f"FIX001 {label} {self.token}"})
			.insert()
			.name
		)

	def user(self, role, business=None):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"fix001-{role.lower().replace(' ', '-')}-{self.token}@example.com",
				"first_name": "FIX001",
				"send_welcome_email": 0,
				"creditflow_business": business,
			}
		).insert()
		user.add_roles(role)
		return user.name

	def customer(self, business, label):
		return frappe.get_doc(
			{
				"doctype": "Customer",
				"business": business,
				"customer_name": f"FIX001 Customer {label} {self.token}",
				"notes": "original",
			}
		).insert()

	def persisted(self, doc):
		return frappe.db.get_value(doc.doctype, doc.name, ["business", "modified"], as_dict=True)

	def api_request(self, path, method, payload=None):
		"""Exercise real HTTP routing/whitelist dispatch in the test transaction."""
		from frappe.api import handle

		request = Request(EnvironBuilder(path=path, method=method, json=payload).get_environ())
		with (
			patch.object(frappe.local, "request", request, create=True),
			patch.object(frappe.local, "form_dict", frappe._dict(payload or {})),
			patch.object(frappe.local, "response", frappe._dict(docs=[])),
		):
			return handle(request).get_json()

	def test_original_in_memory_probe_is_blocked(self):
		self.assertEqual(run_in_memory_probe(), "BLOCKED")

	def test_http_dispatch_rejects_bulk_save_set_value_and_rest_reassignment(self):
		payload = self.customer_b.as_dict()
		payload.update(business=self.business_a, notes="HTTP forged")
		before = self.persisted(self.customer_b)
		result = self.api_request(
			"/api/method/frappe.client.bulk_update",
			"POST",
			{
				"docs": frappe.as_json(
					[
						{
							"doctype": "Customer",
							"docname": self.customer_b.name,
							"business": self.business_a,
							"notes": "HTTP forged",
						}
					]
				),
			},
		)
		self.assertEqual(len(result["message"]["failed_docs"]), 1)
		self.assertIn("PermissionError", result["message"]["failed_docs"][0]["exc"])
		for path, method, data in (
			("/api/method/frappe.client.save", "POST", {"doc": frappe.as_json(payload)}),
			(
				"/api/method/frappe.desk.form.save.savedocs",
				"POST",
				{"doc": frappe.as_json(payload), "action": "Save"},
			),
			(
				"/api/method/frappe.client.set_value",
				"POST",
				{
					"doctype": "Customer",
					"name": self.customer_b.name,
					"fieldname": frappe.as_json({"business": self.business_a, "notes": "HTTP forged"}),
				},
			),
			(
				f"/api/resource/Customer/{self.customer_b.name}",
				"PUT",
				{"business": self.business_a, "notes": "HTTP forged"},
			),
			(
				f"/api/v2/document/Customer/{self.customer_b.name}",
				"PATCH",
				{"business": self.business_a, "notes": "HTTP forged"},
			),
		):
			with self.subTest(path=path), self.assertRaises(frappe.PermissionError):
				self.api_request(path, method, data)
		self.assertEqual(self.persisted(self.customer_b), before)
		self.assertEqual(frappe.db.get_value("Customer", self.customer_b.name, "notes"), "original")

	def test_http_same_tenant_and_cross_tenant_crud(self):
		own_path = f"/api/resource/Customer/{self.customer_a.name}"
		foreign_path = f"/api/resource/Customer/{self.customer_b.name}"
		self.assertEqual(self.api_request(own_path, "GET")["data"]["business"], self.business_a)
		self.api_request(own_path, "PUT", {"notes": "HTTP legitimate"})
		self.assertEqual(frappe.db.get_value("Customer", self.customer_a.name, "notes"), "HTTP legitimate")
		created = self.api_request("/api/resource/Customer", "POST", {"customer_name": f"HTTP {self.token}"})
		self.assertEqual(created["data"]["business"], self.business_a)
		for method, payload in (("GET", None), ("PUT", {"notes": "forged"}), ("DELETE", None)):
			with self.subTest(method=method), self.assertRaises(frappe.PermissionError):
				self.api_request(foreign_path, method, payload)
		with self.assertRaises(frappe.PermissionError):
			self.api_request(own_path, "PUT", {"business": self.business_b})

	def test_attack_a_bulk_reassignment_of_foreign_customer_is_denied(self):
		before = self.persisted(self.customer_b)
		result = client.bulk_update(
			frappe.as_json(
				[
					{
						"doctype": "Customer",
						"docname": self.customer_b.name,
						"business": self.business_a,
						"notes": "attacker-controlled",
					}
				]
			)
		)
		self.assertEqual(len(result["failed_docs"]), 1)
		self.assertIn("PermissionError", result["failed_docs"][0]["exc"])
		self.assertEqual(self.persisted(self.customer_b), before)
		self.assertEqual(frappe.db.get_value("Customer", self.customer_b.name, "notes"), "original")

	def test_attack_b_cross_business_read_edit_and_delete_are_denied(self):
		before = self.persisted(self.customer_b)
		with self.assertRaises(frappe.PermissionError):
			client.get("Customer", self.customer_b.name)
		with self.assertRaises(frappe.PermissionError):
			client.set_value("Customer", self.customer_b.name, "notes", "forged")
		with self.assertRaises(frappe.PermissionError):
			client.delete("Customer", self.customer_b.name)
		self.assertEqual(self.persisted(self.customer_b), before)

	def test_attack_c_same_business_create_read_and_edit_succeed(self):
		created = client.insert({"doctype": "Customer", "customer_name": f"FIX001 own {self.token}"})
		self.assertEqual(created["business"], self.business_a)
		self.assertEqual(client.get("Customer", self.customer_a.name)["business"], self.business_a)
		client.set_value("Customer", self.customer_a.name, "notes", "legitimate")
		result = client.bulk_update(
			frappe.as_json(
				[
					{
						"doctype": "Customer",
						"docname": self.customer_a.name,
						"business": self.business_a,
						"notes": "legitimate bulk edit",
					}
				]
			)
		)
		self.assertEqual(result["failed_docs"], [])
		self.assertEqual(
			frappe.db.get_value("Customer", self.customer_a.name, "notes"), "legitimate bulk edit"
		)

	def test_attack_d_own_customer_cannot_be_transferred_to_other_business(self):
		before = self.persisted(self.customer_a)
		result = client.bulk_update(
			frappe.as_json(
				[
					{
						"doctype": "Customer",
						"docname": self.customer_a.name,
						"business": self.business_b,
					}
				]
			)
		)
		self.assertEqual(len(result["failed_docs"]), 1)
		self.assertEqual(self.persisted(self.customer_a), before)

	def test_attack_e_generic_save_rejects_incoming_tenant_spoof(self):
		before = self.persisted(self.customer_b)
		payload = self.customer_b.as_dict()
		payload.update(business=self.business_a, notes="forged")
		with self.assertRaises(frappe.PermissionError):
			client.save(frappe.as_json(payload))
		self.assertEqual(self.persisted(self.customer_b), before)

	def test_desk_savedocs_rejects_incoming_tenant_spoof(self):
		before = self.persisted(self.customer_b)
		payload = self.customer_b.as_dict()
		payload.update(business=self.business_a, notes="forged")
		with self.assertRaises(frappe.PermissionError):
			savedocs(frappe.as_json(payload), "Save")
		self.assertEqual(self.persisted(self.customer_b), before)

	def test_direct_document_save_rejects_foreign_reassignment(self):
		before = self.persisted(self.customer_b)
		doc = frappe.get_doc("Customer", self.customer_b.name)
		doc.business = self.business_a
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		self.assertEqual(self.persisted(self.customer_b), before)

	def test_bulk_product_and_supplier_reassignment_is_denied(self):
		frappe.set_user("Administrator")
		product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business_b,
				"product_name": "FIX001 Product",
				"reference": f"FIX001-{self.token}",
				"primary_unit": "UNIT",
			}
		).insert()
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": self.business_b,
				"supplier_name": "FIX001 Supplier",
			}
		).insert()
		frappe.set_user(self.owner_a)
		for doc in (product, supplier):
			with self.subTest(doctype=doc.doctype):
				before = self.persisted(doc)
				result = client.bulk_update(
					frappe.as_json(
						[
							{
								"doctype": doc.doctype,
								"docname": doc.name,
								"business": self.business_a,
							}
						]
					)
				)
				self.assertEqual(len(result["failed_docs"]), 1)
				self.assertEqual(self.persisted(doc), before)
				payload = doc.as_dict()
				payload["business"] = self.business_a
				with self.assertRaises(frappe.PermissionError):
					client.save(frappe.as_json(payload))
				with self.assertRaises(frappe.PermissionError):
					savedocs(frappe.as_json(payload), "Save")
				self.assertEqual(self.persisted(doc), before)

	def test_staff_cannot_retarget_an_existing_foreign_sale_draft(self):
		frappe.set_user("Administrator")
		products = []
		for business in (self.business_a, self.business_b):
			products.append(
				frappe.get_doc(
					{
						"doctype": "Product",
						"business": business,
						"product_name": "FIX001 sale product",
						"reference": f"FIX001-{business}-{self.token}",
						"primary_unit": "UNIT",
					}
				).insert()
			)
		sale = frappe.get_doc(
			{
				"doctype": "Sale",
				"business": self.business_b,
				"customer": self.customer_b.name,
				"business_date": frappe.utils.today(),
				"sale_mode": "CREDIT",
				"amount_paid": 0,
				"items": [{"product": products[1].name, "quantity": 1, "unit_price": 5}],
			}
		).insert()
		before = self.persisted(sale)
		frappe.set_user(self.staff_a)
		result = client.bulk_update(
			frappe.as_json(
				[
					{
						"doctype": "Sale",
						"docname": sale.name,
						"business": self.business_a,
						"customer": self.customer_a.name,
						"items": [{"product": products[0].name, "quantity": 1, "unit_price": 5}],
					}
				]
			)
		)
		self.assertEqual(len(result["failed_docs"]), 1)
		self.assertEqual(self.persisted(sale), before)
		self.assertEqual(frappe.db.count("Stock Movement", {"source_sale": sale.name}), 0)
		self.assertEqual(frappe.db.count("Credit Transaction", {"source_sale": sale.name}), 0)

	def test_untrusted_new_flag_or_original_snapshot_cannot_authorize_existing_row(self):
		for fake_new in (False, True):
			with self.subTest(fake_new=fake_new):
				doc = frappe.get_doc(self.customer_b.as_dict())
				doc.business = self.business_a
				doc.set("__islocal", int(fake_new))
				doc._doc_before_save = self.customer_a
				self.assertFalse(has_permission(doc, "write", self.owner_a))

	def test_before_validate_defends_even_when_caller_skips_role_permission_check(self):
		doc = frappe.get_doc(self.customer_b.as_dict())
		doc.business = self.business_a
		with self.assertRaises(frappe.PermissionError):
			set_and_validate_business(doc)
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)

	def test_validation_rechecks_persisted_ownership_after_permission_check(self):
		from creditflow.creditflow.doctype.customer.customer import Customer

		original_check = Customer.check_if_latest

		def change_ownership_after_initial_check(doc):
			original_check(doc)
			# Simulate ownership changing after the early permission check. Do not
			# alter the request document or its original snapshot to match it.
			frappe.db.set_value("Customer", doc.name, "business", self.business_b, update_modified=False)

		doc = frappe.get_doc("Customer", self.customer_a.name)
		doc.notes = "must not persist"
		with patch.object(Customer, "check_if_latest", change_ownership_after_initial_check):
			with self.assertRaises(frappe.PermissionError):
				doc.save()
		self.assertEqual(frappe.db.get_value("Customer", doc.name, "notes"), "original")

	def test_missing_stored_business_is_not_treated_as_a_new_document(self):
		frappe.db.set_value("Customer", self.customer_b.name, "business", None)
		doc = frappe.get_doc("Customer", self.customer_b.name)
		doc.business = self.business_a
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_shared_hooks_deny_stored_foreign_ownership_for_every_tenant_doctype(self):
		from creditflow.hooks import has_permission as permission_hooks
		from creditflow.hooks import permission_query_conditions as tenant_hooks

		for doctype in tenant_hooks:
			with self.subTest(doctype=doctype):
				self.assertEqual(permission_hooks[doctype], "creditflow.permissions.has_permission")
				if doctype == "Business":
					doc = frappe.get_doc("Business", self.business_b)
				else:
					# Minimal persisted drafts exercise the authorization boundary
					# before domain validation or posting can run.
					doc = frappe.new_doc(doctype)
					doc.name = f"FIX001-{frappe.scrub(doctype)}-{self.token}"
					doc.business = self.business_b
					doc.db_insert()
					doc.business = self.business_a
				self.assertFalse(has_permission(doc, "write", self.owner_a))
				with self.assertRaises(frappe.PermissionError):
					set_and_validate_business(doc)
				result = client.bulk_update(
					frappe.as_json(
						[
							{
								"doctype": doctype,
								"docname": doc.name,
								"business": self.business_a,
							}
						]
					)
				)
				self.assertEqual(len(result["failed_docs"]), 1)
				self.assertIn("PermissionError", result["failed_docs"][0]["exc"])

	def test_new_record_cannot_target_foreign_business(self):
		with self.assertRaises(frappe.PermissionError):
			client.insert({"doctype": "Customer", "business": self.business_b, "customer_name": "forged"})

	def test_explicit_platform_administrators_retain_authority(self):
		frappe.set_user("Administrator")
		manager = self.user("System Manager")
		for actor in ("Administrator", manager):
			with self.subTest(actor=actor):
				frappe.set_user(actor)
				doc = frappe.get_doc("Customer", self.customer_b.name)
				doc.business = self.business_a
				doc.save()
				self.assertEqual(self.persisted(doc).business, self.business_a)
				doc.business = self.business_b
				doc.save()
