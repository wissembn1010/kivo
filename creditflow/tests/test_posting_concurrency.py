"""Overlapping real transactions, committed invariants, dedicated synthetic site only."""
from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestPostingConcurrency(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        if frappe.local.site != "kivo-fix001-tests.localhost":
            self.skipTest("Committed concurrency fixtures require the dedicated Kivo test site")
        frappe.set_user("Administrator")
        self.primary = frappe.local.db
        frappe.connect()
        self.secondary = frappe.local.db
        # Exercise ordinary InnoDB REPEATABLE READ current-read semantics, without
        # the optional MariaDB snapshot-isolation abort on unrelated naming rows.
        self.secondary.sql("SET SESSION innodb_snapshot_isolation=OFF")
        frappe.local.db = self.primary
        self.businesses = []
        self.addCleanup(self.cleanup_fixtures)
        token = uuid4().hex[:12]
        self.business = frappe.get_doc({"doctype": "Business", "business_name": f"B06 synthetic {token}"}).insert().name
        self.businesses.append(self.business)
        self.customer = frappe.get_doc({"doctype": "Customer", "business": self.business, "customer_name": f"B06 {token}"}).insert().name
        self.product = frappe.get_doc({"doctype": "Product", "business": self.business, "product_name": f"B06 {token}", "reference": f"B06-{token}", "tva_rate": 0}).insert().name
        opening = frappe.get_doc({"doctype": "Stock Movement", "business": self.business, "product": self.product, "direction": "IN", "quantity": 1, "movement_reason": "OPENING_STOCK", "business_date": frappe.utils.today()}).insert()
        opening.submit()

    def cleanup_fixtures(self):
        self.secondary.rollback()
        self.secondary.close()
        frappe.local.db = self.primary
        self.primary.rollback()
        frappe.set_user("Administrator")
        # Only rows belonging to the explicitly created synthetic Businesses.
        for dt in ("Credit Transaction", "Stock Movement", "Sale Return", "Sale Reversal", "Payment Reversal", "Payment", "Sale", "Customer", "Product"):
            names = frappe.get_all(dt, filters={"business": ["in", self.businesses]}, pluck="name")
            if names:
                for field in frappe.get_meta(dt).get_table_fields():
                    frappe.db.delete(field.options, {"parent": ["in", names], "parenttype": dt})
                frappe.db.delete(dt, {"name": ["in", names]})
        for business in self.businesses:
            frappe.db.delete("Business", {"name": business})
        self.primary.commit()

    def sale(self, credit=False, quantity=1):
        return frappe.get_doc({"doctype": "Sale", "business": self.business, "customer": self.customer if credit else None, "business_date": frappe.utils.today(), "sale_mode": "CREDIT" if credit else "CASH", "amount_paid": 0 if credit else quantity * 100, "payment_method": None if credit else "CASH", "items": [{"product": self.product, "quantity": quantity, "base_price": 100, "unit_price": 100}]}).insert()

    def payment(self):
        return frappe.get_doc({"doctype": "Payment", "business": self.business, "customer": self.customer, "amount": 100, "payment_method": "CASH", "business_date": frappe.utils.today()}).insert()

    def returned(self, sale):
        return frappe.get_doc({"doctype": "Sale Return", "business": self.business, "original_sale": sale.name, "return_date": frappe.utils.today(), "reason": "B06 synthetic", "items": [{"original_sale_item": sale.items[0].name, "quantity": 1}]}).insert()

    def reversal(self, sale):
        return frappe.get_doc({"doctype": "Sale Reversal", "business": self.business, "original_sale": sale.name, "business_date": frappe.utils.today(), "reason": "B06 synthetic"}).insert()

    def interleave(self, winner, contender):
        self.primary.commit()
        frappe.local.db = self.secondary
        self.secondary.rollback()
        # Establish the old view before the competing transaction commits.
        self.assertEqual(self.secondary.sql("SELECT @@tx_isolation")[0][0], "REPEATABLE-READ")
        self.secondary.sql("SELECT name FROM `tabStock Movement` WHERE business=%s", self.business)
        stale = frappe.get_doc(contender.doctype, contender.name)
        frappe.local.db = self.primary
        winner.submit()
        self.primary.commit()
        frappe.local.db = self.secondary
        error = None
        try:
            stale.submit()
            self.secondary.commit()
        except frappe.ValidationError as exc:
            error = str(exc)
            self.secondary.rollback()
        finally:
            frappe.local.db = self.primary
        self.primary.rollback()
        return error

    def balance(self):
        return Decimal(frappe.get_doc("Customer", self.customer).get_ledger_balance())

    def manual_out(self):
        return frappe.get_doc({"doctype": "Stock Movement", "business": self.business, "product": self.product, "direction": "OUT", "quantity": 1, "movement_reason": "MANUAL_ADJUSTMENT", "business_date": frappe.utils.today()}).insert()

    def assert_stock_contender_rejected(self, winner, contender):
        error = self.interleave(winner, contender)
        self.assertIsNotNone(error, "Both last-unit consumers committed")
        self.assertIn("Insufficient stock", error)
        view = frappe.get_doc({"doctype": "Sale", "business": self.business})
        self.assertEqual(view.get_available_stock(self.product), 0)
        self.assertEqual(frappe.db.get_value(contender.doctype, contender.name, "docstatus"), 0)
        self.assertEqual(frappe.db.count("Stock Movement", {"business": self.business, "direction": "OUT", "docstatus": 1}), 1)

    def test_manual_out_after_competing_sale_cannot_overdraw(self):
        self.assert_stock_contender_rejected(self.sale(), self.manual_out())

    def test_sale_after_competing_manual_out_cannot_overdraw(self):
        self.assert_stock_contender_rejected(self.manual_out(), self.sale())

    def test_two_manual_outs_cannot_consume_the_same_last_unit(self):
        self.assert_stock_contender_rejected(self.manual_out(), self.manual_out())

    def test_last_unit_cannot_be_sold_twice_from_stale_snapshot(self):
        first, second = self.sale(), self.sale()
        error = self.interleave(first, second)
        self.assertIsNotNone(error, "Both concurrent last-unit Sales committed")
        self.assertIn("Insufficient stock", error)
        self.assertEqual(first.get_available_stock(self.product), 0)
        self.assertEqual(frappe.db.count("Stock Movement", {"business": self.business, "direction": "OUT"}), 1)
        self.assertEqual(frappe.db.get_value("Sale", second.name, "docstatus"), 0)

    def test_same_debt_cannot_be_paid_twice_from_stale_snapshot(self):
        self.sale(credit=True).submit()
        first, second = self.payment(), self.payment()
        error = self.interleave(first, second)
        self.assertIsNotNone(error, "Both concurrent full-debt Payments committed")
        self.assertIn("outstanding debt", error)
        self.assertEqual(self.balance(), 0)
        self.assertEqual(frappe.db.count("Credit Transaction", {"business": self.business, "transaction_type": "PAYMENT"}), 1)

    def test_credit_limit_sees_competing_committed_sale(self):
        frappe.db.set_value("Stock Movement", {"business": self.business}, "quantity", 2)
        frappe.db.set_value("Customer", self.customer, "credit_limit", 100)
        first, second = self.sale(credit=True), self.sale(credit=True)
        error = self.interleave(first, second)
        self.assertIsNotNone(error, "Concurrent Sales exceeded the credit limit")
        self.assertIn("credit", error.lower())
        self.assertEqual(self.balance(), 100)
        self.assertEqual(first.get_available_stock(self.product), 1)

    def test_returns_cannot_exceed_committed_sold_quantity(self):
        sale = self.sale(); sale.submit()
        first, second = self.returned(sale), self.returned(sale)
        error = self.interleave(first, second)
        self.assertIsNotNone(error, "Both full-quantity Returns committed")
        self.assertIn("remaining returnable", error)
        self.assertEqual(sale.get_available_stock(self.product), 1)

    def test_return_after_competing_payment_creates_refund_not_double_credit(self):
        sale = self.sale(credit=True); sale.submit()
        payment, returned = self.payment(), self.returned(sale)
        self.assertIsNone(self.interleave(payment, returned))
        returned.reload()
        self.assertEqual(self.balance(), 0)
        self.assertEqual(Decimal(str(returned.debt_reduction)), 0)
        self.assertEqual(Decimal(str(returned.refund_amount)), 100)

    def test_payment_after_competing_return_cannot_overpay(self):
        sale = self.sale(credit=True); sale.submit()
        payment, returned = self.payment(), self.returned(sale)
        self.assertIsNotNone(self.interleave(returned, payment))
        self.assertEqual(self.balance(), 0)

    def test_return_and_full_reversal_remain_mutually_exclusive(self):
        sale = self.sale(credit=True); sale.submit()
        returned, reversal = self.returned(sale), self.reversal(sale)
        self.assertIsNotNone(self.interleave(returned, reversal))
        self.assertEqual(sale.get_available_stock(self.product), 1)

    def test_full_reversal_blocks_stale_return(self):
        sale = self.sale(credit=True); sale.submit()
        returned, reversal = self.returned(sale), self.reversal(sale)
        self.assertIsNotNone(self.interleave(reversal, returned))
        self.assertEqual(sale.get_available_stock(self.product), 1)

    def test_full_reversal_cannot_post_twice_from_stale_snapshot(self):
        sale = self.sale(credit=True); sale.submit()
        first, second = self.reversal(sale), self.reversal(sale)
        error = self.interleave(first, second)
        self.assertIsNotNone(error)
        self.assertIn("already been reversed", error)
        self.assertEqual(sale.get_available_stock(self.product), 1)

    def test_valid_competing_sales_with_sufficient_stock_and_credit_both_commit(self):
        frappe.db.set_value("Stock Movement", {"business": self.business}, "quantity", 2)
        frappe.db.set_value("Customer", self.customer, "credit_limit", 200)
        first, second = self.sale(credit=True), self.sale(credit=True)
        self.assertIsNone(self.interleave(first, second))
        self.assertEqual(self.balance(), 200)
        self.assertEqual(first.get_available_stock(self.product), 0)

    def test_partial_returns_share_remaining_debt_without_double_reduction(self):
        frappe.db.set_value("Stock Movement", {"business": self.business}, "quantity", 2)
        sale = self.sale(credit=True, quantity=2)
        sale.sale_mode = "MIXED"; sale.amount_paid = 150; sale.payment_method = "CASH"
        sale.save(); sale.submit()
        first, second = self.returned(sale), self.returned(sale)
        self.assertIsNone(self.interleave(first, second))
        second.reload()
        self.assertEqual(self.balance(), 0)
        self.assertEqual(Decimal(str(first.debt_reduction)), 50)
        self.assertEqual(Decimal(str(second.debt_reduction)), 0)
        self.assertEqual(Decimal(str(second.refund_amount)), 100)
        self.assertEqual(sale.get_available_stock(self.product), 2)

    def test_current_reads_do_not_borrow_another_tenants_stock_or_debt(self):
        other = frappe.get_doc({"doctype": "Business", "business_name": f"B06 other {uuid4().hex[:12]}"}).insert().name
        self.businesses.append(other)
        sale = self.sale(credit=True); sale.submit()
        foreign_view = frappe.get_doc({"doctype": "Sale", "business": other})
        self.assertEqual(foreign_view.get_available_stock(self.product), 0)
        self.assertEqual(foreign_view.get_customer_current_debt(self.customer), 0)
        payment = frappe.get_doc({"doctype": "Payment", "business": other, "customer": self.customer, "amount": 1})
        self.assertEqual(payment.get_outstanding_debt(), 0)
        with self.assertRaisesRegex(frappe.ValidationError, "same Business"):
            payment.insert()

    def test_split_return_rounding_uses_competing_committed_amounts(self):
        product = frappe.get_doc("Product", self.product)
        product.tva_rate = 19
        product.save()
        sale = self.sale(credit=True)
        sale.items[0].base_price = 2.55
        sale.items[0].unit_price = 2.55
        sale.save()
        sale.submit()
        returns = []
        for unused in range(2):
            ret = self.returned(sale)
            ret.items[0].quantity = 0.5
            ret.save()
            returns.append(ret)
        self.assertIsNone(self.interleave(*returns))
        rows = [frappe.get_doc("Sale Return", ret.name) for ret in returns]
        for field, source in (("return_ht", "total_ht"), ("return_tva", "total_tva"), ("return_ttc", "total_ttc")):
            self.assertEqual(sum(Decimal(str(row.get(field))) for row in rows), Decimal(str(sale.get(source))))
        self.assertEqual(sum(Decimal(str(row.refund_amount)) for row in rows), 0)
        self.assertEqual(self.balance(), 0)
