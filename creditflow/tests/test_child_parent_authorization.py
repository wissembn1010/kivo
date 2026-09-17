from uuid import uuid4

import frappe
from frappe import client
from frappe.tests import IntegrationTestCase

from creditflow.tests import test_stored_business_authorization as stored_tests

RELATIONSHIPS = {
	"Sale Item": ("Sale", "items"),
	"Purchase Item": ("Purchase", "items"),
	"Sale Return Item": ("Sale Return", "items"),
	"Product UOM Conversion": ("Product", "uom_conversions"),
	"TEJ Export Batch Item": ("TEJ Export Batch", "records"),
	"Onboarding Import Result": ("Onboarding Import Batch", "created_records"),
	"CreditFlow Plan Entitlement": ("CreditFlow Plan", "entitlements"),
}


class IntegrationTestChildParentAuthorization(IntegrationTestCase):
	api_request = stored_tests.IntegrationTestStoredBusinessAuthorization.api_request

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, "Administrator")
		self.token = uuid4().hex[:10]
		self.tenants = []
		for label in ("A", "B"):
			business = frappe.get_doc(
				{"doctype": "Business", "business_name": f"Child {label} {self.token}"}
			).insert()
			customer = frappe.get_doc(
				{"doctype": "Customer", "business": business.name, "customer_name": label}
			).insert()
			product = frappe.get_doc(
				{
					"doctype": "Product",
					"business": business.name,
					"product_name": label,
					"reference": f"CH-{label}-{self.token}",
					"primary_unit": "UNIT",
				}
			).insert()
			sale = frappe.get_doc(
				{
					"doctype": "Sale",
					"business": business.name,
					"customer": customer.name,
					"business_date": frappe.utils.today(),
					"sale_mode": "CREDIT",
					"items": [{"product": product.name, "quantity": 1, "unit_price": 5}],
				}
			).insert()
			self.tenants.append(frappe._dict(business=business.name, product=product.name, sale=sale))
		self.a, self.b = self.tenants
		self.users = {}
		for role in ("STAFF", "OWNER"):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": f"child-{role.lower()}-{self.token}@example.com",
					"first_name": "Child test",
					"send_welcome_email": 0,
					"creditflow_business": self.a.business,
				}
			).insert()
			user.add_roles(role)
			self.users[role] = user.name
		frappe.set_user(self.users["STAFF"])

	def snapshot(self, row):
		return frappe.db.get_value(row.doctype, row.name, "*", as_dict=True)

	def bulk(self, row, **values):
		return client.bulk_update(frappe.as_json([{"doctype": row.doctype, "docname": row.name, **values}]))

	def assert_bulk_denied(self, row, **values):
		before = self.snapshot(row)
		result = self.bulk(row, **values)
		self.assertEqual(len(result["failed_docs"]), 1, result)
		self.assertIn("PermissionError", result["failed_docs"][0]["exc"])
		self.assertEqual(self.snapshot(row), before)

	def test_exact_staff_bulk_foreign_reparent_probe(self):
		self.assert_bulk_denied(
			self.b.sale.items[0],
			parent=self.a.sale.name,
			parenttype="Sale",
			parentfield="items",
			product=self.a.product,
			quantity=9,
		)

	def test_foreign_modification_and_parent_metadata(self):
		row = self.b.sale.items[0]
		for values in (
			{"quantity": 9},
			{"parent": self.a.sale.name},
			{"parenttype": "Purchase"},
			{"parentfield": "invalid"},
		):
			with self.subTest(values=values):
				self.assert_bulk_denied(row, **values)

	def test_own_child_cannot_move_to_foreign_or_same_tenant_parent(self):
		row = self.a.sale.items[0]
		self.assert_bulk_denied(row, parent=self.b.sale.name)
		other = frappe.copy_doc(self.a.sale).insert()
		self.assert_bulk_denied(row, parent=other.name)

	def test_direct_api_save_and_resource_updates(self):
		row = self.b.sale.items[0]
		payload = row.as_dict()
		payload.update(parent=self.a.sale.name, product=self.a.product, quantity=9)
		before = self.snapshot(row)
		for path, method, data in (
			("/api/method/frappe.client.save", "POST", {"doc": frappe.as_json(payload)}),
			(f"/api/resource/Sale Item/{row.name}", "PUT", payload),
			(f"/api/v2/document/Sale Item/{row.name}", "PATCH", payload),
			(
				"/api/method/frappe.client.set_value",
				"POST",
				{
					"doctype": "Sale Item",
					"name": row.name,
					"fieldname": frappe.as_json({"quantity": 9, "parent": self.a.sale.name}),
				},
			),
		):
			with self.subTest(path=path), self.assertRaises(frappe.PermissionError):
				self.api_request(path, method, data)
			self.assertEqual(self.snapshot(row), before)

	def test_foreign_read_and_delete(self):
		row = self.b.sale.items[0]
		before = self.snapshot(row)
		with self.assertRaises(frappe.PermissionError):
			client.get(row.doctype, row.name)
		with self.assertRaises((frappe.PermissionError, frappe.DoesNotExistError)):
			client.delete(row.doctype, row.name)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc(row.doctype, row.name)
		forged = frappe.get_doc(row.as_dict())
		forged.parent = self.a.sale.name
		with self.assertRaises(frappe.PermissionError):
			forged.delete()
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc(row.doctype, [row.name])
		self.assertEqual(self.snapshot(row), before)

	def test_embedded_foreign_child_id_cannot_be_taken_over(self):
		row = self.b.sale.items[0]
		before = self.snapshot(row)
		doc = frappe.get_doc("Sale", self.a.sale.name)
		payload = row.as_dict()
		payload.update(product=self.a.product, quantity=9)
		doc.append("items", payload)
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		self.assertEqual(self.snapshot(row), before)

	def test_low_level_document_mutations_are_guarded(self):
		row = self.b.sale.items[0]
		before = self.snapshot(row)
		for operation in ("db_update", "db_set", "save"):
			doc = frappe.get_doc(row.as_dict())
			doc.parent = self.a.sale.name
			with self.subTest(operation=operation), self.assertRaises(frappe.PermissionError):
				if operation == "db_set":
					doc.db_set("quantity", 9)
				elif operation == "save":
					doc.save(ignore_permissions=True)
				else:
					doc.db_update()
			self.assertEqual(self.snapshot(row), before)

	def test_supported_same_tenant_edit_create_remove(self):
		row = self.a.sale.items[0]
		client.set_value("Sale Item", row.name, "quantity", 2)
		self.assertEqual(frappe.db.get_value("Sale Item", row.name, "quantity"), 2)
		result = client.insert(
			{
				"doctype": "Sale Item",
				"parent": self.a.sale.name,
				"parenttype": "Sale",
				"parentfield": "items",
				"product": self.a.product,
				"quantity": 1,
				"unit_price": 5,
			}
		)
		self.assertEqual(len(result["items"]), 2)
		added = result["items"][-1]["name"]
		client.delete("Sale Item", added)
		self.assertFalse(frappe.db.exists("Sale Item", added))
		self.assertEqual(self.bulk(frappe.get_doc("Sale Item", row.name), quantity=3)["failed_docs"], [])

	def test_foreign_creation_and_invalid_destination(self):
		for metadata in (
			{"parent": self.b.sale.name, "parenttype": "Sale", "parentfield": "items"},
			{"parent": self.a.sale.name, "parenttype": "Purchase", "parentfield": "items"},
			{"parent": self.a.sale.name, "parenttype": "Sale", "parentfield": "invalid"},
			{"parent": "missing-parent", "parenttype": "Sale", "parentfield": "items"},
		):
			doc = frappe.get_doc(
				{
					"doctype": "Sale Item",
					**metadata,
					"product": self.a.product,
					"quantity": 1,
					"unit_price": 5,
				}
			)
			with (
				self.subTest(metadata=metadata),
				self.assertRaises((frappe.PermissionError, frappe.DoesNotExistError)),
			):
				doc.insert()

	def test_all_child_tables_reject_foreign_rows_and_relationship_changes(self):
		from unittest.mock import patch

		frappe.set_user("Administrator")
		tax = frappe.new_doc("Withholding Tax")
		tax.name = f"CH-TAX-{self.token}"
		tax.business = self.b.business
		tax.db_insert()
		values = {
			"Sale Item": {"product": self.b.product, "quantity": 1, "unit_price": 5},
			"Purchase Item": {"product": self.b.product, "quantity": 1, "unit_cost": 5},
			"Sale Return Item": {"original_sale_item": self.b.sale.items[0].name, "quantity": 1},
			"Product UOM Conversion": {"uom": "UNIT", "conversion_factor": 1},
			"TEJ Export Batch Item": {"withholding_tax": tax.name, "action": "ADD"},
			"CreditFlow Plan Entitlement": {"entitlement_key": "test_limit", "value_type": "INTEGER"},
		}
		cases = []
		for child, (parent, field) in RELATIONSHIPS.items():
			parents = []
			for tenant in self.tenants:
				doc = frappe.new_doc(parent)
				doc.name = f"CH-{frappe.scrub(parent)}-{tenant.business}-{self.token}"
				if doc.meta.has_field("business"):
					doc.business = tenant.business
				doc.db_insert()
				parents.append(doc)
			row = frappe.new_doc(child)
			row.name = f"CH-{frappe.scrub(child)}-{self.token}"
			row.update(dict(parent=parents[1].name, parenttype=parent, parentfield=field))
			row.update(values.get(child, {}))
			row.db_insert()
			cases.append((row, parents[0]))
		frappe.set_user(self.users["OWNER"])
		for row, parent in cases:
			with self.subTest(child=row.doctype):
				# Replay the previous behavior in memory, with only this new guard
				# disabled. All rows are synthetic and rolled back before the real
				# denial assertion. Read-only parent roles were already protective.
				frappe.db.savepoint("child_baseline_probe")
				try:
					with patch("creditflow.child_permissions.authorize_child"):
						if row.doctype not in {"Onboarding Import Result", "CreditFlow Plan Entitlement"}:
							self.assertEqual(self.bulk(row, parent=parent.name)["failed_docs"], [])
						else:
							previous = frappe.get_doc(row.as_dict())
							previous.parent = parent.name
							previous.db_update()
						self.assertEqual(frappe.db.get_value(row.doctype, row.name, "parent"), parent.name)
				finally:
					frappe.db.rollback(save_point="child_baseline_probe")
				self.assert_bulk_denied(row, parent=parent.name)
				forged = frappe.get_doc(row.as_dict())
				forged.parent = parent.name
				with self.assertRaises(frappe.PermissionError):
					forged.db_update()

	def test_schema_inventory_is_covered(self):
		import json
		from pathlib import Path

		root = Path(frappe.get_app_path("creditflow", "creditflow", "doctype"))
		children = {
			d["name"] for p in root.glob("*/*.json") if (d := json.loads(p.read_text())).get("istable")
		}
		self.assertEqual(children, set(RELATIONSHIPS))

	def test_child_list_queries_cannot_disclose_foreign_rows(self):
		rows = client.get_list("Sale Item", parent="Sale", fields=["name", "parent"], limit_page_length=500)
		self.assertNotIn(self.b.sale.items[0].name, {row.name for row in rows})
		self.assertIn(self.a.sale.items[0].name, {row.name for row in rows})

	def test_untrusted_snapshots_and_new_flag_do_not_hide_persisted_parent(self):
		row = self.b.sale.items[0]
		for local in (0, 1):
			doc = frappe.get_doc(row.as_dict())
			doc.parent = self.a.sale.name
			doc.set("__islocal", local)
			doc._doc_before_save = self.a.sale.items[0]
			doc.parent_doc = self.a.sale
			with self.subTest(local=local), self.assertRaises(frappe.PermissionError):
				doc.db_update()

	def test_parent_relationship_is_rechecked_under_lock(self):
		from unittest.mock import patch

		row = self.a.sale.items[0]
		doc = frappe.get_doc(row.as_dict())
		original = frappe.db.get_value
		changed = False

		def lookup(doctype, name=None, *args, **kwargs):
			nonlocal changed
			value = original(doctype, name, *args, **kwargs)
			if doctype == "Sale Item" and name == row.name and not changed and not kwargs.get("for_update"):
				changed = True
				frappe.db.set_value(doctype, name, "parent", self.b.sale.name, update_modified=False)
			return value

		with patch.object(frappe.db, "get_value", side_effect=lookup):
			with self.assertRaises(frappe.PermissionError):
				doc.db_update()
		self.assertTrue(changed)
		self.assertEqual(frappe.db.get_value("Sale Item", row.name, "quantity"), 1)

	def test_administrator_preserves_valid_relationship_management(self):
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Sale Item", self.b.sale.items[0].name)
		doc.parent = self.a.sale.name
		doc.product = self.a.product
		doc.save()
		self.assertEqual(frappe.db.get_value("Sale Item", doc.name, "parent"), self.a.sale.name)
		doc.parenttype = "Customer"
		with self.assertRaises(frappe.PermissionError):
			doc.db_update()

	def test_shared_plan_child_read_allowed_but_owner_mutation_denied(self):
		frappe.set_user("Administrator")
		plan = frappe.new_doc("CreditFlow Plan")
		plan.name = f"CH-PLAN-{self.token}"
		plan.db_insert()
		row = frappe.new_doc("CreditFlow Plan Entitlement")
		row.update(dict(parent=plan.name, parenttype="CreditFlow Plan", parentfield="entitlements"))
		row.db_insert()
		frappe.set_user(self.users["OWNER"])
		self.assertEqual(client.get(row.doctype, row.name)["name"], row.name)
		with self.assertRaises(frappe.PermissionError):
			row.db_update()

	def test_foreign_child_creation_via_parent_api_is_denied(self):
		with self.assertRaises(frappe.PermissionError):
			client.insert(
				{
					"doctype": "Sale Item",
					"parent": self.b.sale.name,
					"parenttype": "Sale",
					"parentfield": "items",
					"product": self.b.product,
					"quantity": 1,
					"unit_price": 5,
				}
			)

	def test_child_resource_deletion_uses_persisted_parent(self):
		row = self.b.sale.items[0]
		for path in (f"/api/resource/Sale Item/{row.name}", f"/api/v2/document/Sale Item/{row.name}"):
			with (
				self.subTest(path=path),
				self.assertRaises((frappe.PermissionError, frappe.DoesNotExistError)),
			):
				self.api_request(path, "DELETE", {"parent": self.a.sale.name})
		self.assertTrue(frappe.db.exists("Sale Item", row.name))
