import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe import client
from frappe.model.rename_doc import rename_doc
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request


class IntegrationTestSharedUOMAuthorization(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, "Administrator")
		self.token = uuid4().hex[:10]
		self.unit = frappe.get_doc(
			{
				"doctype": "CreditFlow UOM",
				"uom_code": f"P0-{self.token}",
				"uom_name": "Shared test unit",
				"active": 1,
			}
		).insert()
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"LAST P0 A {self.token}"}
		).insert()
		self.owner = self.make_user("owner", "OWNER", self.business.name)

	def make_user(self, label, role, business=None):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"last-p0-{label}-{self.token}@example.com",
				"first_name": "LAST P0",
				"send_welcome_email": 0,
				"creditflow_business": business,
			}
		).insert()
		user.add_roles(role)
		return user

	def api_request(self, path, method, payload=None):
		from frappe.api import handle

		request = Request(EnvironBuilder(path=path, method=method, json=payload).get_environ())
		with (
			patch.object(frappe.local, "request", request, create=True),
			patch.object(frappe.local, "form_dict", frappe._dict(payload or {})),
			patch.object(frappe.local, "response", frappe._dict(docs=[])),
		):
			return handle(request).get_json()

	def persisted_unit(self):
		return frappe.db.get_value(
			"CreditFlow UOM", self.unit.name, ["uom_code", "uom_name", "active", "modified"], as_dict=True
		)

	def test_owner_bulk_cannot_deactivate_shared_unit(self):
		before = frappe.db.get_value(
			"CreditFlow UOM", self.unit.name, ["active", "modified"], as_dict=True
		)
		frappe.set_user(self.owner.name)
		result = client.bulk_update(
			frappe.as_json(
				[{"doctype": "CreditFlow UOM", "docname": self.unit.name, "active": 0}]
			)
		)
		after = frappe.db.get_value(
			"CreditFlow UOM", self.unit.name, ["active", "modified"], as_dict=True
		)
		self.assertEqual((len(result["failed_docs"]), after), (1, before))

	def test_tenant_client_and_rest_mutations_are_denied(self):
		staff = self.make_user("staff", "STAFF", self.business.name)
		before = self.persisted_unit()
		payload = self.unit.as_dict()
		payload.update(active=0, uom_name="Changed by tenant")
		new_unit = {"doctype": "CreditFlow UOM", "uom_code": f"NEW-{self.token}", "uom_name": "New"}
		for actor in (self.owner.name, staff.name):
			frappe.set_user(actor)
			for path, method, data in (
				("/api/method/frappe.client.insert", "POST", {"doc": new_unit}),
				("/api/method/frappe.client.save", "POST", {"doc": payload}),
				("/api/method/frappe.client.set_value", "POST", {"doctype": "CreditFlow UOM", "name": self.unit.name, "fieldname": "active", "value": 0}),
				("/api/method/frappe.client.rename_doc", "POST", {"doctype": "CreditFlow UOM", "old_name": self.unit.name, "new_name": f"NEW-{self.token}"}),
				("/api/method/frappe.client.rename_doc", "POST", {"doctype": "CreditFlow UOM", "old_name": self.unit.name, "new_name": "UNIT", "merge": True}),
				("/api/resource/CreditFlow UOM", "POST", new_unit),
				(f"/api/resource/CreditFlow UOM/{self.unit.name}", "PUT", {"active": 0}),
				(f"/api/v2/document/CreditFlow UOM/{self.unit.name}", "PATCH", {"active": 0}),
				(f"/api/resource/CreditFlow UOM/{self.unit.name}", "DELETE", None),
				(f"/api/v2/document/CreditFlow UOM/{self.unit.name}", "DELETE", None),
			):
				with self.subTest(actor=actor, path=path, method=method):
					with self.assertRaises(frappe.PermissionError):
						self.api_request(path, method, data)
					self.assertEqual(self.persisted_unit(), before)
					self.assertFalse(frappe.db.exists("CreditFlow UOM", f"NEW-{self.token}"))

	def test_permission_bypass_flags_and_document_writes_are_denied(self):
		before = self.persisted_unit()
		frappe.set_user(self.owner.name)
		for operation in ("save", "db_update", "db_set", "delete", "rename", "insert", "db_insert"):
			with self.subTest(operation=operation):
				doc = frappe.get_doc("CreditFlow UOM", self.unit.name)
				doc.active = 0
				doc.flags.ignore_permissions = True
				doc.flags.ignore_validate = True
				with self.assertRaises(frappe.PermissionError):
					if operation == "db_set":
						doc.db_set("active", 0)
					elif operation == "delete":
						frappe.delete_doc(doc.doctype, doc.name, ignore_permissions=True)
					elif operation == "rename":
						rename_doc(doc.doctype, doc.name, f"NEW-{self.token}", ignore_permissions=True)
					elif operation in ("insert", "db_insert"):
						new = frappe.get_doc({"doctype": doc.doctype, "name": f"NEW-{self.token}", "uom_code": f"NEW-{self.token}", "uom_name": "New"})
						new.flags.ignore_permissions = True
						getattr(new, operation)()
					else:
						getattr(doc, operation)()
				self.assertEqual(self.persisted_unit(), before)
				self.assertFalse(frappe.db.exists("CreditFlow UOM", f"NEW-{self.token}"))

	def test_stale_or_custom_role_grants_cannot_override_platform_boundary(self):
		original = frappe.permissions.get_role_permissions

		def permissive_uom(meta, *args, **kwargs):
			perms = original(meta, *args, **kwargs).copy()
			if meta.name == "CreditFlow UOM":
				perms.update(create=1, write=1, delete=1)
			return perms

		frappe.set_user(self.owner.name)
		before = self.persisted_unit()
		with patch.object(frappe.permissions, "get_role_permissions", side_effect=permissive_uom):
			for action in ("create", "write", "delete"):
				self.assertFalse(frappe.has_permission("CreditFlow UOM", action, doc=self.unit))
			with self.assertRaises(frappe.PermissionError):
				client.set_value("CreditFlow UOM", self.unit.name, "active", 0)
		self.assertEqual(self.persisted_unit(), before)

	def test_platform_administrators_retain_catalogue_maintenance(self):
		manager = self.make_user("manager", "System Manager")
		for index, actor in enumerate(("Administrator", manager.name)):
			with self.subTest(actor=actor):
				frappe.set_user(actor)
				code = f"ADMIN-{index}-{self.token}"
				doc = frappe.get_doc({"doctype": "CreditFlow UOM", "uom_code": code, "uom_name": "Admin unit", "active": 1}).insert()
				client.set_value(doc.doctype, doc.name, "active", 0)
				self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, "active"), 0)
				doc.reload().db_set("active", 1)
				new_name = frappe.rename_doc(doc.doctype, doc.name, f"RENAMED-{code}", rebuild_search=False)
				self.assertTrue(frappe.db.exists(doc.doctype, new_name))
				frappe.delete_doc(doc.doctype, new_name)
				self.assertFalse(frappe.db.exists(doc.doctype, new_name))

	def test_both_tenants_can_read_units_edit_conversions_and_sell(self):
		other = frappe.get_doc({"doctype": "Business", "business_name": f"LAST P0 B {self.token}"}).insert()
		owner_b = self.make_user("owner-b", "OWNER", other.name)
		staff = self.make_user("staff", "STAFF", self.business.name)
		before = self.persisted_unit()
		for business, owner in ((self.business, self.owner), (other, owner_b)):
			frappe.set_user(owner.name)
			self.assertEqual(client.get("CreditFlow UOM", self.unit.name)["active"], 1)
			self.assertTrue(client.get_list("CreditFlow UOM", filters={"name": self.unit.name}))
			product = frappe.get_doc({"doctype": "Product", "business": business.name, "product_name": "Shared unit product", "reference": f"P0-{business.name}-{self.token}", "primary_unit": self.unit.name, "selling_price": 5}).insert()
			product.append("uom_conversions", {"uom": "BOX", "conversion_factor": 2})
			product.save()
			product.uom_conversions[0].conversion_factor = 3
			product.save()
			self.assertEqual(product.reload().uom_conversions[0].conversion_factor, 3)
			customer = frappe.get_doc({"doctype": "Customer", "business": business.name, "customer_name": "P0 customer"}).insert()
			frappe.get_doc({"doctype": "Stock Movement", "business": business.name, "product": product.name, "direction": "IN", "quantity": 3, "movement_reason": "OPENING_STOCK", "business_date": frappe.utils.today()}).insert().submit()
			if business.name == self.business.name:
				frappe.set_user(staff.name)
				self.assertEqual(client.get("CreditFlow UOM", self.unit.name)["active"], 1)
				sale_actor = staff.name
			else:
				sale_actor = owner.name
			with self.subTest(sale_actor=sale_actor):
				sale = frappe.get_doc({"doctype": "Sale", "business": business.name, "customer": customer.name, "business_date": frappe.utils.today(), "sale_mode": "CREDIT", "amount_paid": 0, "items": [{"product": product.name, "quantity": 1, "uom": "BOX", "unit_price": 5}]}).insert().submit()
				self.assertEqual(sale.items[0].base_quantity, 3)
				self.assertEqual(frappe.db.count("Stock Movement", {"source_sale": sale.name, "docstatus": 1}), 1)
				self.assertEqual(float(customer.get_ledger_balance()), 5)
				self.assertEqual(self.persisted_unit(), before)

	def test_shipped_tenant_permissions_are_read_only(self):
		path = Path(frappe.get_app_path("creditflow", "creditflow", "doctype", "creditflow_uom", "creditflow_uom.json"))
		meta = json.loads(path.read_text())
		for role in ("OWNER", "STAFF"):
			perms = next(row for row in meta["permissions"] if row["role"] == role)
			self.assertEqual(perms["read"], 1)
			self.assertFalse(any(perms.get(action) for action in ("create", "write", "delete")))
