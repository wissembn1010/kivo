"""DDL and legacy-data upgrade evidence; synthetic test site only."""
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.modules.patch_handler import PatchType, get_patches_from_app

from creditflow.patches.v16_0.scope_pilot_identifiers import execute as scope_identifiers
from creditflow.patches.v16_0.normalize_payment_methods import execute as normalize_methods
from creditflow.pilot_integrity import ensure_scoped_identifiers, invoice_series_key
from creditflow.install import after_install
from creditflow.tests import test_pilot_integrity_concurrency as concurrency_support


class IntegrationTestPilotIntegrityMigration(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        if frappe.local.site != "kivo-fix001-tests.localhost":
            self.skipTest("DDL/committed fixtures require the dedicated synthetic site")
        frappe.set_user("Administrator")
        self.primary = frappe.local.db
        self.businesses = []
        self.addCleanup(self.cleanup)
        token = uuid4().hex[:12]
        self.business = frappe.get_doc({"doctype": "Business", "business_name": f"Pilot migration {token}"}).insert().name
        self.businesses.append(self.business)
        self.product = frappe.get_doc({"doctype": "Product", "business": self.business,
            "product_name": "Historical product", "reference": f"OLD-{token}", "legacy_product_id": f"LEGACY-{token}"}).insert()
        self.blank_product = frappe.get_doc({"doctype": "Product", "business": self.business,
            "product_name": "Historical blank ID", "reference": f"BLANK-{token}"}).insert()
        frappe.db.set_value("Product", self.blank_product.name, "legacy_product_id", "", update_modified=False)
        movement = frappe.get_doc({"doctype": "Stock Movement", "business": self.business,
            "product": self.product.name, "direction": "IN", "quantity": 10,
            "movement_reason": "OPENING_STOCK", "business_date": "2026-01-01"}).insert()
        movement.submit()
        self.sale = self.make_sale()
        frappe.db.set_value("Sale", self.sale.name, {"invoice_number": "FAC-2026-00073",
            "payment_method": " bank transfer "}, update_modified=False)
        customer = frappe.get_doc({"doctype": "Customer", "business": self.business,
            "customer_name": "Historical credit customer"}).insert()
        credit_sale = frappe.get_doc({"doctype": "Sale", "business": self.business,
            "customer": customer.name, "business_date": "2026-01-01", "sale_mode": "CREDIT",
            "amount_paid": 0, "items": [{"product": self.product.name, "quantity": 1,
            "base_price": 1, "unit_price": 1}]}).insert()
        credit_sale.submit()
        frappe.db.set_value("Sale", credit_sale.name, "invoice_number", "FAC-2026-00072", update_modified=False)
        self.primary.sql("DELETE FROM `tabSeries` WHERE name=%s", invoice_series_key(self.business, "2026"))
        self.primary.commit()

    def cleanup(self):
        self.primary.rollback()
        # Recover scoped constraints even when an assertion fails after DDL.
        ensure_scoped_identifiers()
        for doctype in ("Product", "Sale"):
            frappe.reload_doc("creditflow", "doctype", frappe.scrub(doctype), force=True)
        frappe.db.delete("Customer", {"business": ["in", self.businesses]})
        concurrency_support.IntegrationTestPilotIntegrityConcurrency.cleanup(self)

    def make_sale(self):
        sale = frappe.get_doc({"doctype": "Sale", "business": self.business,
            "business_date": "2026-01-01", "sale_mode": "CASH", "amount_paid": 1,
            "payment_method": "CASH", "items": [{"product": self.product.name,
            "quantity": 1, "base_price": 1, "unit_price": 1}]}).insert()
        sale.submit()
        return sale

    def indexes(self, doctype):
        rows = frappe.db.sql(f"SHOW INDEX FROM `tab{doctype}` WHERE Non_unique=0", as_dict=True)
        result = {}
        for row in rows:
            result.setdefault(row.Key_name, []).append(row.Column_name)
        return result

    def test_upgrade_old_global_constraints_preserves_history_and_replays(self):
        self.assertIn("creditflow.patches.v16_0.scope_pilot_identifiers",
            get_patches_from_app("creditflow", patch_type=PatchType.pre_model_sync))
        self.assertIn("creditflow.patches.v16_0.normalize_payment_methods",
            get_patches_from_app("creditflow", patch_type=PatchType.post_model_sync))
        snapshots = {dt: frappe.get_all(dt, filters={"business": self.business}, fields=["*"])
            for dt in ("Product", "Sale", "Stock Movement", "Credit Transaction", "Customer")}
        # Reconstruct the CURRENT HEAD schema: single-column global unique indexes.
        for dt, field, index in (("Product", "reference", "business_reference_unique"),
            ("Product", "legacy_product_id", "business_legacy_product_id_unique"),
            ("Sale", "invoice_number", "business_invoice_number_unique")):
            self.primary.sql(f"ALTER TABLE `tab{dt}` ADD UNIQUE INDEX `{field}` (`{field}`)")
            self.primary.sql(f"ALTER TABLE `tab{dt}` DROP INDEX `{index}`")
        scope_identifiers()
        for dt in ("Product", "Sale"):
            frappe.reload_doc("creditflow", "doctype", frappe.scrub(dt), force=True)
        normalize_methods(); self.primary.commit()
        scope_identifiers(); normalize_methods(); self.primary.commit()
        for dt in snapshots:
            after = frappe.get_all(dt, filters={"business": self.business}, fields=["*"])
            if dt == "Sale":
                for row in snapshots[dt]:
                    if row.name == self.sale.name:
                        row.payment_method = "BANK_TRANSFER"
            if dt == "Product":
                for row in snapshots[dt]:
                    if row.legacy_product_id == "":
                        row.legacy_product_id = None
            self.assertEqual(after, snapshots[dt], f"Unexpected historical mutation in {dt}")
        self.assertEqual(self.indexes("Product")["business_reference_unique"], ["business", "reference"])
        self.assertEqual(self.indexes("Product")["business_legacy_product_id_unique"], ["business", "legacy_product_id"])
        self.assertEqual(self.indexes("Sale")["business_invoice_number_unique"], ["business", "invoice_number"])
        self.assertNotIn("reference", self.indexes("Product"))
        self.assertNotIn("legacy_product_id", self.indexes("Product"))
        self.assertNotIn("invoice_number", self.indexes("Sale"))
        future = self.make_sale()
        self.assertEqual(future.invoice_number, "FAC-2026-00074")
        self.primary.commit()
        # Fresh install uses this same schema helper through the install hook.
        self.assertIn("creditflow.install.after_install", frappe.get_hooks("after_install", app_name="creditflow"))
        after_install(); after_install()
        self.assertEqual(self.make_sale().invoice_number, "FAC-2026-00075")
        self.primary.commit()
        print("PILOT MIGRATION: legacy rows preserved; aliases canonicalized; replay idempotent; next invoices 00074/00075")

    def test_duplicate_preflight_fails_before_schema_or_data_changes(self):
        self.primary.sql("ALTER TABLE `tabProduct` DROP INDEX `business_reference_unique`")
        duplicate = frappe.get_doc(self.product.as_dict()); duplicate.name = uuid4().hex
        duplicate.legacy_product_id = None; duplicate.db_insert()
        self.primary.commit()
        before = self.indexes("Product")
        try:
            with self.assertRaisesRegex(frappe.ValidationError, "duplicate identifiers within a Business"):
                scope_identifiers()
            self.assertEqual(self.indexes("Product"), before)
            self.assertEqual(frappe.db.count("Product", {"business": self.business, "reference": self.product.reference}), 2)
        finally:
            frappe.db.delete("Product", {"name": duplicate.name}); self.primary.commit()
            ensure_scoped_identifiers()
        print("PILOT MIGRATION: duplicate preflight rejected before first DDL")
