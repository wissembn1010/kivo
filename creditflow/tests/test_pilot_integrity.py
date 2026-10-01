"""Pilot integrity regressions against real Frappe/MariaDB documents."""
from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.checkout import get_customer_context
from creditflow.onboarding import _validate_row, _resolve
from creditflow.payment_methods import (
    CUSTOMER_PAYMENT_METHODS, SUPPLIER_PAYMENT_METHODS, migrate_payment_methods,
    normalize_payment_method,
)
from creditflow.pilot_integrity import invoice_series_key, seed_invoice_series


class IntegrationTestPilotIntegrity(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        frappe.set_user("Administrator")
        self.token = uuid4().hex[:12]
        self.business = self.make_business("A")
        self.other = self.make_business("B")
        self.customer = frappe.get_doc({"doctype": "Customer", "business": self.business,
            "customer_name": "Pilot customer", "credit_limit": 100, "price_type": "PROFESSIONAL"}).insert()
        self.product = self.make_product(self.business, "MAIN")
        movement = frappe.get_doc({"doctype": "Stock Movement", "business": self.business,
            "product": self.product.name, "direction": "IN", "quantity": 100,
            "movement_reason": "OPENING_STOCK", "business_date": frappe.utils.today()}).insert()
        movement.submit()
        self.addCleanup(frappe.set_user, "Administrator")

    def make_business(self, suffix):
        return frappe.get_doc({"doctype": "Business", "business_name": f"Pilot {suffix} {self.token}"}).insert().name

    def make_product(self, business, reference, legacy=None):
        return frappe.get_doc({"doctype": "Product", "business": business,
            "product_name": "Pilot product", "reference": f"{reference}-{self.token}",
            "legacy_product_id": legacy}).insert()

    def sale(self, business=None, product=None, *, year=2026, submit=True, quantity=1):
        doc = frappe.get_doc({"doctype": "Sale", "business": business or self.business,
            "business_date": f"{year}-09-01", "sale_mode": "CASH", "amount_paid": quantity,
            "payment_method": "CASH", "items": [{"product": product or self.product.name,
            "quantity": quantity, "unit_price": 1}]}).insert()
        if submit:
            doc.submit()
        return doc

    def test_product_identifiers_scoped_and_optional(self):
        a = self.make_product(self.business, "SHARED", "LEGACY-1")
        b = self.make_product(self.other, "SHARED", "LEGACY-1")
        self.assertNotEqual(a.name, b.name)
        with self.assertRaisesRegex(frappe.ValidationError, "this Business"):
            self.make_product(self.business, "SHARED", "LEGACY-2")
        with self.assertRaisesRegex(frappe.ValidationError, "this Business"):
            self.make_product(self.business, "NEW", "LEGACY-1")
        c = self.make_product(self.business, "BLANK", "")
        d = self.make_product(self.business, "BLANK2", "")
        self.assertIsNone(frappe.db.get_value("Product", c.name, "legacy_product_id"))
        a.product_name = "Updated"; a.save(); a.reload()
        self.assertEqual(a.reference, b.reference)
        d.reference = a.reference
        with self.assertRaisesRegex(frappe.ValidationError, "this Business"):
            d.save()

    def test_import_check_and_resolution_are_scoped(self):
        sku = self.product.reference
        data = _validate_row("PRODUCTS", {"product_name": "Imported", "reference": sku}, self.other)
        b = frappe.get_doc(dict(data, doctype="Product", business=self.other)).insert()
        self.assertEqual(_resolve("Product", sku, self.business, "reference"), self.product.name)
        self.assertEqual(_resolve("Product", sku, self.other, "reference"), b.name)
        with self.assertRaisesRegex(ValueError, "this Business"):
            _validate_row("PRODUCTS", {"product_name": "Duplicate", "reference": sku}, self.business)
        with self.assertRaises(ValueError):
            _resolve("Product", self.product.name, self.other, "reference")

    def test_invoice_per_business_year_and_failed_submit_rollback(self):
        first = self.sale()
        bproduct = self.make_product(self.other, "B")
        movement = frappe.get_doc({"doctype": "Stock Movement", "business": self.other,
            "product": bproduct.name, "direction": "IN", "quantity": 10,
            "movement_reason": "OPENING_STOCK", "business_date": frappe.utils.today()}).insert()
        movement.submit()
        second_business = self.sale(self.other, bproduct.name)
        self.assertEqual(first.invoice_number, "FAC-2026-00001")
        self.assertEqual(second_business.invoice_number, first.invoice_number)
        self.assertEqual(self.sale(year=2027).invoice_number, "FAC-2027-00001")
        draft = self.sale(submit=False, quantity=200)
        frappe.db.savepoint("pilot_failed_submit")
        with self.assertRaisesRegex(frappe.ValidationError, "Insufficient stock"):
            draft.submit()
        frappe.db.rollback(save_point="pilot_failed_submit")
        self.assertEqual(frappe.db.get_value("Sale", draft.name, ["docstatus", "invoice_number"]), (0, None))
        self.assertEqual(self.sale().invoice_number, "FAC-2026-00002")
        forged = self.sale(submit=False)
        forged.invoice_number = "FAC-2026-99999"
        with self.assertRaisesRegex(frappe.ValidationError, "generated by the system"):
            forged.submit()
        first.reload()
        self.assertEqual(first.invoice_number, "FAC-2026-00001")

    def test_historical_invoice_seeding_preserves_numbers_and_never_rewinds(self):
        sale = self.sale()
        frappe.db.set_value("Sale", sale.name, "invoice_number", "fac-2026-00421 ", update_modified=False)
        before = frappe.db.get_value("Sale", sale.name, ["invoice_number", "total_amount", "docstatus", "seller_business_name"])
        seed_invoice_series(); seed_invoice_series()
        self.assertEqual(self.sale().invoice_number, "FAC-2026-00422")
        seed_invoice_series()
        self.assertEqual(self.sale().invoice_number, "FAC-2026-00423")
        self.assertEqual(frappe.db.get_value("Sale", sale.name, ["invoice_number", "total_amount", "docstatus", "seller_business_name"]), before)
        self.assertEqual(frappe.db.sql("SELECT current FROM `tabSeries` WHERE name=%s", invoice_series_key(self.business, "2026"))[0][0], 423)

    def ledger(self, direction, amount):
        doc = frappe.get_doc({"doctype": "Credit Transaction", "business": self.business,
            "customer": self.customer.name, "transaction_type": "OPENING_BALANCE",
            "direction": direction, "amount": amount, "transaction_date": frappe.utils.today()}).insert()
        doc.submit()
        return doc

    def test_authoritative_context_over_1000_rows_and_unlimited_credit(self):
        # Ordinary ledger documents prove posted-only aggregation and both signs.
        self.ledger("DEBIT", "25.123"); self.ledger("CREDIT", "5.001")
        # Populate >1000 small posted synthetic rows without 1000 controller runs.
        template = self.ledger("DEBIT", "0.001").as_dict()
        for _ in range(1000):
            doc = frappe.get_doc(dict(template, name=uuid4().hex)); doc.db_insert()
        self.assertGreater(frappe.db.count("Credit Transaction", {"customer": self.customer.name, "docstatus": 1}), 1000)
        draft = frappe.get_doc(dict(template, name=uuid4().hex, docstatus=0, amount=999)); draft.db_insert()
        context = get_customer_context(self.business, self.customer.name)
        self.assertEqual(context["current_debt"], "21.123")
        self.assertEqual(context["available_credit"], "78.877")
        self.assertEqual(context["price_type"], "PROFESSIONAL")
        self.customer.credit_limit = 0; self.customer.save()
        self.assertIsNone(get_customer_context(self.business, self.customer.name)["available_credit"])
        self.customer.credit_limit = 10; self.customer.save()
        self.assertEqual(get_customer_context(self.business, self.customer.name)["available_credit"], "-11.123")

    def test_context_owner_staff_foreign_guest_authorization(self):
        foreign = frappe.get_doc({"doctype": "Customer", "business": self.other, "customer_name": "Foreign"}).insert()
        for role in ("OWNER", "STAFF"):
            user = frappe.get_doc({"doctype": "User", "email": f"pilot-{role.lower()}-{self.token}@example.com",
                "first_name": "Pilot", "send_welcome_email": 0, "creditflow_business": self.business}).insert()
            user.add_roles(role)
            frappe.set_user(user.name)
            self.assertEqual(get_customer_context(self.business, self.customer.name)["current_debt"], "0.000")
            with self.assertRaises(frappe.PermissionError):
                get_customer_context(self.other, foreign.name)
            with self.assertRaises(frappe.PermissionError):
                get_customer_context(self.business, foreign.name)
            with self.assertRaises(frappe.PermissionError):
                get_customer_context(self.other, self.customer.name)
            frappe.set_user("Administrator")
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            get_customer_context(self.business, self.customer.name)

    def test_vocabulary_normalization_and_workflow_boundaries(self):
        for value in ("BANK TRANSFER", "bank_transfer", " bank transfer "):
            self.assertEqual(normalize_payment_method(value, CUSTOMER_PAYMENT_METHODS), "BANK_TRANSFER")
            purchase = frappe.get_doc({"doctype": "Purchase", "payment_status": "PAID",
                "payment_method": value, "total_amount": 10})
            purchase.validate_payment()
            self.assertEqual(purchase.payment_method, "BANK_TRANSFER")
            supplier = frappe.get_doc({"doctype": "Supplier Payment", "payment_method": value})
            supplier.validate_payment_method()
            self.assertEqual(supplier.payment_method, "BANK_TRANSFER")
            sale = frappe.get_doc({"doctype": "Sale", "sale_mode": "MIXED", "customer": self.customer.name,
                "payment_method": value, "total_amount": 10, "amount_paid": 5})
            sale.validate_sale_payment_terms()
            self.assertEqual(sale.payment_method, "BANK_TRANSFER")
            payment = frappe.get_doc({"doctype": "Payment", "payment_method": value})
            payment.validate_payment_method()
            self.assertEqual(payment.payment_method, "BANK_TRANSFER")
        with self.assertRaises(frappe.ValidationError):
            normalize_payment_method("TRAITE", CUSTOMER_PAYMENT_METHODS)
        with self.assertRaises(frappe.ValidationError):
            normalize_payment_method("CARD", SUPPLIER_PAYMENT_METHODS)
        with self.assertRaises(frappe.ValidationError):
            normalize_payment_method("bogus", CUSTOMER_PAYMENT_METHODS)
        payment.payment_method = "CHEQUE"
        with self.assertRaises(frappe.ValidationError):
            payment.validate_payment_method()
        sale.sale_mode = "CASH"; sale.amount_paid = 10
        with self.assertRaises(frappe.ValidationError):
            sale.validate_sale_payment_terms()

    def test_historical_payment_alias_migration_is_idempotent(self):
        sale = self.sale()
        frappe.db.set_value("Sale", sale.name, "payment_method", " bank transfer ", update_modified=False)
        before = frappe.db.get_value("Sale", sale.name, ["modified", "amount_paid", "total_amount", "invoice_number", "docstatus"])
        migrate_payment_methods(); migrate_payment_methods()
        self.assertEqual(frappe.db.get_value("Sale", sale.name, "payment_method"), "BANK_TRANSFER")
        self.assertEqual(frappe.db.get_value("Sale", sale.name, ["modified", "amount_paid", "total_amount", "invoice_number", "docstatus"]), before)

    def test_posted_payment_and_purchase_store_canonical_methods(self):
        self.ledger("DEBIT", 50)
        payment = frappe.get_doc({"doctype": "Payment", "business": self.business,
            "customer": self.customer.name, "amount": 10, "payment_method": " bank transfer ",
            "business_date": frappe.utils.today()}).insert()
        payment.submit(); payment.reload()
        self.assertEqual(payment.payment_method, "BANK_TRANSFER")
        self.assertEqual(self.customer.get_ledger_balance(), "40.000")
        supplier = frappe.get_doc({"doctype": "Supplier", "business": self.business,
            "supplier_name": "Pilot supplier"}).insert()
        purchase = frappe.get_doc({"doctype": "Purchase", "business": self.business,
            "supplier": supplier.name, "business_date": frappe.utils.today(), "payment_status": "PAID",
            "payment_method": "BANK TRANSFER", "items": [{"product": self.product.name,
            "quantity": 1, "unit_cost": 10}]}).insert()
        purchase.submit(); purchase.reload()
        self.assertEqual(purchase.payment_method, "BANK_TRANSFER")
        self.assertEqual(frappe.db.get_value("Supplier Payment", {"source_purchase": purchase.name},
            ["payment_method", "docstatus"]), ("BANK_TRANSFER", 1))
        self.assertEqual(supplier.get_ledger_balance(), "0.000")
