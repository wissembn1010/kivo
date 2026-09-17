from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.tests import test_sale_returns as fixtures
from creditflow.creditflow.doctype.sale_return.sale_return import money


class IntegrationTestReturnRounding(IntegrationTestCase):
    setUp = fixtures.IntegrationTestSaleReturns.setUp
    tearDown = fixtures.IntegrationTestSaleReturns.tearDown
    returned = fixtures.IntegrationTestSaleReturns.returned
    balance = fixtures.IntegrationTestSaleReturns.balance

    def split_sale(self, price, mode):
        ht = money(Decimal(str(price)) * Decimal("1.5"))
        paid = ht + money(ht * Decimal("0.19")) if mode == "CASH" else 0
        sale = frappe.get_doc(dict(doctype="Sale", business=self.business.name,
            customer=self.customer.name, business_date=frappe.utils.today(), sale_mode=mode,
            amount_paid=paid, payment_method="CASH" if mode == "CASH" else None,
            items=[dict(product=self.product.name, quantity=1.5, base_price=price, unit_price=price)]))
        sale.insert()
        if mode == "CASH":
            sale.amount_paid = sale.total_ttc
        sale.submit()
        return sale

    def check_splits(self, price, mode):
        sale = self.split_sale(price, mode)
        original = sale.items[0]
        caps = [Decimal(str(original.get(field))) for field in ("line_ht", "tva_amount", "line_ttc")]
        totals = [Decimal(0)] * 3
        # Drafts prepared together must be recalculated against committed returns.
        drafts = [self.returned(sale, 0.5, submit=False) for unused in range(3)]
        financial = Decimal(0)
        for draft in drafts:
            draft.submit()
            ret = frappe.get_doc("Sale Return", draft.name)
            for index, field in enumerate(("return_ht", "return_tva", "return_ttc")):
                amount = Decimal(str(ret.get(field)))
                self.assertGreaterEqual(amount, 0)
                totals[index] += amount
                self.assertLessEqual(totals[index], caps[index])
            self.assertEqual(Decimal(str(ret.return_ht)) + Decimal(str(ret.return_tva)), Decimal(str(ret.return_ttc)))
            financial += Decimal(str(ret.refund_amount)) + Decimal(str(ret.debt_reduction))
        self.assertEqual(totals, caps)
        self.assertEqual(financial, caps[2])
        self.assertEqual(self.balance(), 0)
        if mode == "CREDIT":
            self.assertTrue(all(frappe.db.get_value("Sale Return", doc.name, "refund_amount") == 0 for doc in drafts))

    def test_three_fractional_cash_returns_cannot_exceed_original_tax(self):
        self.check_splits(1.7, "CASH")

    def test_three_fractional_credit_returns_do_not_create_excess_refund(self):
        self.check_splits(1.7, "CREDIT")

    def test_final_return_absorbs_both_upward_and_downward_rounding(self):
        self.check_splits(1.699, "CREDIT")
