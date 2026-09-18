from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestSaleFiscalInvoice(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        token = uuid4().hex[:10]
        self.business = frappe.get_doc({"doctype": "Business", "business_name": f"Fiscal Business {token}", "address": "1 Rue de Tunis", "phone": "71123456", "email": f"fiscal-{token}@example.com", "tax_identifier": f"MF-{token}", "commercial_registration": f"RC-{token}"}).insert()
        self.customer = frappe.get_doc({"doctype": "Customer", "business": self.business.name, "customer_name": f"Fiscal Customer {token}", "address": "2 Rue Client", "phone": "22123456"}).insert()
        self.products = [self.product(token, "ZERO", 0), self.product(token, "SEVEN", 7), self.product(token, "NINETEEN", 19)]
        for product in self.products:
            movement = frappe.get_doc({"doctype": "Stock Movement", "business": self.business.name, "product": product.name, "direction": "IN", "quantity": 1000, "movement_reason": "OPENING_STOCK", "business_date": frappe.utils.today()}).insert()
            movement.submit()

    def tearDown(self):
        frappe.set_user("Administrator")

    def product(self, token, label, rate):
        return frappe.get_doc({"doctype": "Product", "business": self.business.name, "product_name": f"{label} Product", "reference": f"FISC-{label}-{token}", "tva_rate": rate}).insert()

    def sale(self, items, mode="CREDIT", paid=0, customer=True, submit=True):
        doc = frappe.get_doc({"doctype": "Sale", "business": self.business.name, "customer": self.customer.name if customer else None, "business_date": frappe.utils.today(), "sale_mode": mode, "amount_paid": paid, "payment_method": "CASH" if paid else None, "items": items})
        if submit:
            doc.insert(); doc.submit()
        return doc

    def item(self, product, quantity, price, discount=0, uom=None):
        return {"product": product.name, "quantity": quantity, "unit_price": price, "discount_percent": discount, "uom": uom}

    def test_form_preview_matches_submit_and_reload(self):
        from creditflow.creditflow.doctype.sale.sale import preview_totals

        for mode in ("CASH", "MIXED"):
            items = [self.item(self.products[2], 1, 100)]
            preview = preview_totals(self.business.name, items, self.customer.name)
            self.assertEqual(preview["total_amount"], Decimal("119.000"))
            sale = self.sale(items, mode=mode, paid=preview["total_amount"] if mode == "CASH" else 50)
            sale.reload()
            self.assertEqual(Decimal(str(sale.total_amount)), preview["total_amount"])
            self.assertEqual(Decimal(str(sale.outstanding_amount)), Decimal(0 if mode == "CASH" else 69))

    def test_form_preview_discount_rounding_and_no_writes(self):
        from creditflow.creditflow.doctype.sale.sale import preview_totals

        doctypes = ("Sale", "Sale Item", "Stock Movement", "Credit Transaction")
        before = {dt: frappe.db.count(dt) for dt in doctypes}
        items = [self.item(self.products[1], 1, 100), self.item(self.products[2], 2, 100, 10)]
        preview = preview_totals(self.business.name, frappe.as_json(items), self.customer.name)
        self.assertEqual(preview["total_amount"], Decimal("321.200"))
        items = [dict(self.item(self.products[2], 100, 1), base_price="1.005", discount_percent="0.5", tva_rate=0, line_ttc=0)]
        preview = preview_totals(self.business.name, items, self.customer.name)
        # Discounted unit prices round first, as in the existing Sale controller.
        self.assertEqual(preview["total_amount"], Decimal("119.000"))
        self.assertEqual(preview["items"][0]["tva_rate"], Decimal(19))
        # Legacy unit-price-only rows retain full precision until line rounding.
        preview = preview_totals(self.business.name, [self.item(self.products[2], 100, "0.999975")])
        self.assertEqual(preview["total_amount"], Decimal("118.998"))
        self.assertEqual({dt: frappe.db.count(dt) for dt in doctypes}, before)

    def test_form_preview_enforces_role_and_linked_tenants(self):
        from creditflow.creditflow.doctype.sale.sale import preview_totals

        token = uuid4().hex[:10]
        other = frappe.get_doc({"doctype": "Business", "business_name": f"Preview Other {token}"}).insert()
        customer = frappe.get_doc({"doctype": "Customer", "business": other.name, "customer_name": "Other"}).insert()
        product = frappe.get_doc({"doctype": "Product", "business": other.name, "product_name": "Other", "reference": f"PREVIEW-{token}"}).insert()
        user = frappe.get_doc({"doctype": "User", "email": f"preview-{token}@example.com", "first_name": "Preview", "send_welcome_email": 0, "creditflow_business": self.business.name}).insert()
        user.add_roles("STAFF")
        frappe.set_user(user.name)
        items = [self.item(self.products[2], 1, 100)]
        self.assertEqual(preview_totals(self.business.name, items)["total_amount"], Decimal("119.000"))
        with self.assertRaises(frappe.PermissionError):
            preview_totals(other.name, [self.item(product, 1, 100)])
        with self.assertRaisesRegex(frappe.ValidationError, "same Business"):
            preview_totals(self.business.name, items, customer.name)
        with self.assertRaisesRegex(frappe.ValidationError, "same Business"):
            preview_totals(self.business.name, [self.item(product, 1, 100)])
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            preview_totals(self.business.name, items)

    def test_zero_and_standard_tva_snapshots(self):
        zero = self.sale([self.item(self.products[0], 2, 100)])
        self.assertEqual((Decimal(str(zero.total_ht)), Decimal(str(zero.total_tva)), Decimal(str(zero.total_ttc))), (Decimal("200.000"), Decimal("0.000"), Decimal("200.000")))
        taxable = self.sale([self.item(self.products[2], 2, 100)])
        self.assertEqual((Decimal(str(taxable.total_ht)), Decimal(str(taxable.total_tva)), Decimal(str(taxable.total_ttc))), (Decimal("200.000"), Decimal("38.000"), Decimal("238.000")))

    def test_mixed_rates_and_discount_before_tva(self):
        sale = self.sale([self.item(self.products[1], 1, 100), self.item(self.products[2], 2, 100, 10)])
        self.assertEqual(Decimal(str(sale.items[1].line_ht)), Decimal("180.000"))
        self.assertEqual(Decimal(str(sale.items[1].tva_amount)), Decimal("34.200"))
        self.assertEqual((Decimal(str(sale.total_ht)), Decimal(str(sale.total_tva)), Decimal(str(sale.total_ttc))), (Decimal("280.000"), Decimal("41.200"), Decimal("321.200")))

    def test_cash_credit_and_mixed_use_ttc_for_economics(self):
        cash = self.sale([self.item(self.products[2], 1, 100)], mode="CASH", paid=119, customer=False)
        self.assertEqual(Decimal(str(cash.outstanding_amount)), Decimal("0.000"))
        credit = self.sale([self.item(self.products[2], 1, 100)])
        mixed = self.sale([self.item(self.products[2], 1, 100)], mode="MIXED", paid=50)
        self.assertEqual(Decimal(str(credit.outstanding_amount)), Decimal("119.000"))
        self.assertEqual(Decimal(str(mixed.outstanding_amount)), Decimal("69.000"))
        debit = frappe.db.get_value("Credit Transaction", {"source_sale": mixed.name}, "amount")
        self.assertEqual(Decimal(str(debit)), Decimal("69.000"))

    def test_product_changes_do_not_change_submitted_snapshot(self):
        sale = self.sale([self.item(self.products[2], 2, 100, 10)])
        before_rate = sale.items[0].tva_rate
        before_price = sale.items[0].unit_price
        before_total = sale.total_ttc
        self.products[2].tva_rate = 7; self.products[2].selling_price = 999; self.products[2].save()
        saved = frappe.get_doc("Sale", sale.name)
        self.assertEqual(saved.items[0].tva_rate, before_rate)
        self.assertEqual(saved.items[0].unit_price, before_price)
        self.assertEqual(Decimal(str(saved.total_ttc)), before_total)

    def test_invoice_number_is_unique_stable_and_not_forgeable(self):
        first = self.sale([self.item(self.products[0], 1, 1)])
        second = self.sale([self.item(self.products[0], 1, 1)])
        self.assertRegex(first.invoice_number, r"^FAC-\d{4}-\d{5}$")
        self.assertNotEqual(first.invoice_number, second.invoice_number)
        self.assertEqual(frappe.get_doc("Sale", first.name).invoice_number, first.invoice_number)
        staff_email = f"fiscal-staff-{uuid4().hex[:10]}@example.com"
        staff = frappe.get_doc({"doctype": "User", "email": staff_email, "first_name": "Fiscal Staff", "send_welcome_email": 0, "creditflow_business": self.business.name}).insert(ignore_permissions=True)
        staff.add_roles("STAFF")
        frappe.set_user(staff.name)
        forged = self.sale([self.item(self.products[0], 1, 1)], submit=False); forged.invoice_number = "FAC-2099-99999"
        with self.assertRaisesRegex(frappe.ValidationError, "generated by the system"):
            forged.insert()
        frappe.set_user("Administrator")
        first.invoice_number = "FORGED"
        with self.assertRaises(frappe.UpdateAfterSubmitError):
            first.save()

    def test_business_and_product_changes_do_not_change_invoice_snapshots(self):
        sale = self.sale([self.item(self.products[0], 1, 10)])
        self.business.business_name = "Changed Seller"; self.business.tax_identifier = "CHANGED"; self.business.save()
        saved = frappe.get_doc("Sale", sale.name)
        self.assertNotEqual(saved.seller_business_name, "Changed Seller")
        self.assertNotEqual(saved.seller_tax_identifier, "CHANGED")

    def test_reversal_preserves_invoice_and_tax_and_cannot_repeat(self):
        sale = self.sale([self.item(self.products[2], 2, 100)])
        invoice, total = sale.invoice_number, sale.total_ttc
        reversal = frappe.get_doc({"doctype": "Sale Reversal", "original_sale": sale.name, "business": self.business.name, "business_date": frappe.utils.today(), "reason": "Test"}).insert(); reversal.submit()
        original = frappe.get_doc("Sale", sale.name)
        self.assertEqual((original.invoice_number, original.total_ttc), (invoice, total))
        with self.assertRaisesRegex(frappe.ValidationError, "already been reversed"):
            frappe.get_doc({"doctype": "Sale Reversal", "original_sale": sale.name, "business": self.business.name, "business_date": frappe.utils.today(), "reason": "Again"}).insert()

    def test_invoice_print_contains_snapshotted_fiscal_data(self):
        sale = self.sale([self.item(self.products[2], 2, 100, 10)])
        html = frappe.get_print("Sale", sale.name, "Fiscal Invoice")
        for value in (sale.invoice_number, sale.seller_business_name, sale.seller_tax_identifier, sale.invoice_customer_name, "NINETEEN Product", "180.000", "34.200", "214.200"):
            self.assertIn(str(value), html)

    def test_submitted_invoice_print_preserves_large_millime_value(self):
        sale = self.sale([self.item(self.products[0], 1, 100)])
        amount = Decimal("10000000000000.001")
        # Simulate an exact DECIMAL value in an existing submitted record.
        frappe.db.sql("UPDATE `tabSale Item` SET line_ttc=10000000000000.001 WHERE name=%s", sale.items[0].name)
        frappe.db.sql("UPDATE `tabSale` SET total_ttc=10000000000000.001 WHERE name=%s", sale.name)
        self.assertEqual(frappe.db.sql("SELECT CAST(total_ttc AS CHAR) FROM `tabSale` WHERE name=%s", sale.name)[0][0], str(amount))
        self.assertEqual(frappe.db.sql("SELECT CAST(line_ttc AS CHAR) FROM `tabSale Item` WHERE name=%s", sale.items[0].name)[0][0], str(amount))

        html = frappe.get_print("Sale", sale.name, "Fiscal Invoice")

        self.assertIn("10000000000000.001", html)
        self.assertNotIn("10000000000000.002", html)

    def test_uom_quantity_and_tax_are_independent(self):
        self.products[2].append("uom_conversions", {"uom": "BOX", "conversion_factor": 12}); self.products[2].save()
        sale = self.sale([self.item(self.products[2], 2, 100, uom="BOX")])
        self.assertEqual(Decimal(str(sale.items[0].base_quantity)), Decimal("24"))
        self.assertEqual(Decimal(str(sale.total_ttc)), Decimal("238.000"))
