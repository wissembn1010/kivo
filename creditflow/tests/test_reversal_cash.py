from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow import dashboard
from creditflow.tests import test_sale_returns as fixtures


class IntegrationTestReversalCash(IntegrationTestCase):
    setUp = fixtures.IntegrationTestSaleReturns.setUp
    tearDown = fixtures.IntegrationTestSaleReturns.tearDown
    sale = fixtures.IntegrationTestSaleReturns.sale
    returned = fixtures.IntegrationTestSaleReturns.returned
    balance = fixtures.IntegrationTestSaleReturns.balance

    def reversal(self, sale):
        return frappe.get_doc(dict(doctype="Sale Reversal", business=self.business.name,
            original_sale=sale.name, business_date=frappe.utils.today(), reason="B07 synthetic"))

    def cash(self):
        return dashboard.cash_collected_today()["value"]

    def revenue(self):
        return dashboard.net_sales_today()["value"]

    def effects(self):
        return (frappe.db.count("Stock Movement", {"business": self.business.name}),
            frappe.db.count("Credit Transaction", {"business": self.business.name}), self.balance())

    def test_upfront_cash_and_mixed_sale_reversals_rejected_without_effects(self):
        for mode, paid in (("CASH", 119), ("MIXED", 40)):
            sale = self.sale(mode=mode, paid=paid, qty=1)
            before = (self.effects(), self.cash(), self.revenue())
            with self.assertRaisesRegex(frappe.ValidationError, "Sale Return"):
                self.reversal(sale).insert().submit()
            self.assertEqual((self.effects(), self.cash(), self.revenue()), before)
            self.assertEqual(frappe.db.count("Sale Reversal", {"original_sale": sale.name, "docstatus": 1}), 0)

    def test_credit_reversal_restores_stock_debt_and_revenue_but_not_cash(self):
        cash, revenue = self.cash(), self.revenue()
        sale = self.sale(qty=1)
        snapshots = (sale.total_ht, sale.total_tva, sale.total_ttc)
        self.reversal(sale).insert().submit()
        self.assertEqual(self.balance(), 0)
        self.assertEqual(sale.get_available_stock(self.product.name), 1000)
        self.assertEqual((self.cash(), self.revenue()), (cash, revenue))
        sale.reload()
        self.assertEqual((sale.total_ht, sale.total_tva, sale.total_ttc), snapshots)
        before = self.effects()
        with self.assertRaisesRegex(frappe.ValidationError, "already been reversed"):
            self.reversal(sale).insert().submit()
        self.assertEqual(self.effects(), before)

    def test_later_partial_payment_retained_until_explicit_payment_reversal(self):
        cash, revenue = self.cash(), self.revenue()
        sale = self.sale(qty=1)
        payment = frappe.get_doc(dict(doctype="Payment", business=self.business.name,
            customer=self.customer.name, amount=40, payment_method="CASH", business_date=frappe.utils.today())).insert()
        payment.submit()
        self.reversal(sale).insert().submit()
        self.assertEqual(self.balance(), -40)
        self.assertEqual(self.cash(), cash + 40)
        self.assertEqual(self.revenue(), revenue)
        reversal = dict(doctype="Payment Reversal", business=self.business.name,
            original_payment=payment.name, business_date=frappe.utils.today(), reason="Correct receipt")
        frappe.get_doc(reversal).insert().submit()
        self.assertEqual((self.balance(), self.cash(), self.revenue()), (0, cash, revenue))
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc(reversal).insert().submit()
        self.assertEqual(self.cash(), cash)

    def test_paid_return_changes_cash_only_on_confirmed_cash_refund(self):
        cash, revenue = self.cash(), self.revenue()
        sale = self.sale(mode="CASH", paid=119, qty=1)
        ret = self.returned(sale, 1)
        self.assertEqual((self.cash(), self.revenue()), (cash + 119, revenue))
        with self.assertRaises(frappe.ValidationError):
            self.reversal(sale).insert().submit()
        before = self.effects()
        ret.refund_status = "REFUNDED"; ret.refund_method = "CASH"; ret.refund_date = frappe.utils.today()
        ret.save(); ret.save()
        self.assertEqual(self.effects(), before)
        self.assertEqual((self.cash(), self.revenue()), (cash, revenue))
        with self.assertRaises(frappe.ValidationError):
            self.reversal(sale).insert().submit()
        self.assertEqual((self.cash(), self.revenue()), (cash, revenue))

    def test_bank_refund_does_not_reduce_cash(self):
        cash = self.cash()
        sale = self.sale(mode="CASH", paid=119, qty=1)
        ret = self.returned(sale, 1)
        ret.refund_status = "REFUNDED"; ret.refund_method = "BANK_TRANSFER"; ret.refund_date = frappe.utils.today()
        ret.save()
        self.assertEqual(self.cash(), cash + 119)

    def test_historical_paid_reversal_is_not_evidence_of_cash_refund(self):
        cash, revenue = self.cash(), self.revenue()
        sale = self.sale(mode="CASH", paid=119, qty=1)
        # Reporting fixture for a historical reversal, bypassing new-posting rules.
        legacy = self.reversal(sale)
        legacy.name = "B07-" + uuid4().hex
        legacy.docstatus = 1
        legacy.db_insert()
        self.assertEqual(self.cash(), cash + 119)
        self.assertEqual(self.revenue(), revenue)
        self.assertEqual(frappe.db.get_value("Sale", sale.name, "amount_paid"), 119)
