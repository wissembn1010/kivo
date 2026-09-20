from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.modules.patch_handler import PatchType, get_patches_from_app

from creditflow.install import ensure_supplier_transaction_index
from creditflow.patches.v16_0.add_supplier_transaction_index import execute


class IntegrationTestSupplierTransactionIndex(IntegrationTestCase):
    def assert_index(self):
        rows = frappe.db.sql(
            "SHOW INDEX FROM `tabSupplier Transaction` WHERE Key_name = 'supplier_index'",
            as_dict=True,
        )
        self.assertEqual(
            [row.Column_name for row in sorted(rows, key=lambda row: row.Seq_in_index)],
            ["business", "supplier", "docstatus"],
        )

    def test_00_index_present_after_site_lifecycle(self):
        # Do not create the index here: installation/migration must have done it.
        self.assert_index()

    def check_missing_index_lifecycle(self, lifecycle):
        # DDL commits implicitly. Roll back before DDL and create data only after
        # schema setup, so the test never commits its business/payment fixtures.
        frappe.db.rollback()
        frappe.db.sql("ALTER TABLE `tabSupplier Transaction` DROP INDEX IF EXISTS `supplier_index`")
        try:
            self.assertFalse(frappe.db.has_index("tabSupplier Transaction", "supplier_index"))
            lifecycle()
            self.assert_index()
            lifecycle()
            self.assert_index()

            business = frappe.get_doc({
                "doctype": "Business", "business_name": f"Index lifecycle {uuid4().hex[:10]}"
            }).insert()
            supplier = frappe.get_doc({
                "doctype": "Supplier", "business": business.name, "supplier_name": "Index supplier"
            }).insert()
            debt = frappe.get_doc({
                "doctype": "Supplier Transaction", "business": business.name,
                "supplier": supplier.name, "transaction_type": "OPENING_BALANCE",
                "direction": "DEBIT", "amount": 100, "transaction_date": frappe.utils.today(),
            }).insert()
            debt.submit()
            payment = frappe.get_doc({
                "doctype": "Supplier Payment", "business": business.name,
                "supplier": supplier.name, "amount": 25, "business_date": frappe.utils.today(),
                "payment_method": "CASH",
            }).insert()
            payment.submit()
            self.assertEqual(payment.get_outstanding_debt(), Decimal("75.000"))
            payment.amount = 76
            with self.assertRaisesRegex(frappe.ValidationError, "outstanding debt"):
                payment.validate_not_overpayment()
        finally:
            frappe.db.rollback()
            ensure_supplier_transaction_index()

    def test_fresh_install_hook_creates_index_and_validates_debt(self):
        hooks = frappe.get_hooks("after_install", app_name="creditflow")
        self.assertIn("creditflow.install.after_install", hooks)

        def install_hooks():
            for hook in hooks:
                frappe.get_attr(hook)()

        self.check_missing_index_lifecycle(install_hooks)

    def test_upgrade_patch_creates_index_and_is_idempotent(self):
        self.assertIn(
            "creditflow.patches.v16_0.add_supplier_transaction_index",
            get_patches_from_app("creditflow", patch_type=PatchType.post_model_sync),
        )
        self.check_missing_index_lifecycle(execute)
