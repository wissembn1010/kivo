"""B-08: real submitted-document API saves, synthetic fixtures only."""
from uuid import uuid4

import frappe
from frappe import client
from frappe.tests import IntegrationTestCase

from creditflow.tests import test_sale_returns as fixtures
from creditflow.subscription import clear_request_cache


class IntegrationTestRefundUpdates(IntegrationTestCase):
    setUp = fixtures.IntegrationTestSaleReturns.setUp
    tearDown = fixtures.IntegrationTestSaleReturns.tearDown
    sale = fixtures.IntegrationTestSaleReturns.sale
    returned = fixtures.IntegrationTestSaleReturns.returned
    make_user = fixtures.IntegrationTestSaleReturns.make_user
    balance = fixtures.IntegrationTestSaleReturns.balance

    def pending(self):
        return self.returned(self.sale(mode="MIXED", paid=50, qty=1), 1)

    def completion(self):
        return dict(refund_status="REFUNDED", refund_method="CASH", refund_date=frappe.utils.today(), refund_reference="B08")

    def update(self, ret, values, api="set_value"):
        if api == "set_value":
            return client.set_value("Sale Return", ret.name, values)
        doc = frappe.get_doc("Sale Return", ret.name).as_dict()
        doc.update(values)
        return client.save(doc)

    def test_missing_completion_fields_rejected_by_both_apis(self):
        ret = self.pending()
        for api in ("set_value", "save"):
            for missing in ("refund_method", "refund_date"):
                with self.subTest(api=api, missing=missing):
                    values = self.completion(); values[missing] = None
                    with self.assertRaises(frappe.ValidationError):
                        self.update(ret, values, api)
                    self.assertEqual(frappe.db.get_value("Sale Return", ret.name, "refund_status"), "REFUND_DUE")

    def test_owner_completes_once_without_reposting_and_completed_edits_fail(self):
        ret = self.pending()
        before = (frappe.db.count("Stock Movement"), frappe.db.count("Credit Transaction"), self.balance())
        owner = self.make_user("OWNER")
        frappe.set_user(owner)
        self.update(ret, self.completion(), "save")
        self.update(ret, self.completion())  # Identical retry is harmless.
        for values in ({"refund_status": "REFUND_DUE"}, {"refund_method": "OTHER"}, {"refund_date": frappe.utils.add_days(frappe.utils.today(), 1)}, {"refund_reference": "changed"}):
            with self.subTest(values=values):
                with self.assertRaises(frappe.ValidationError):
                    self.update(ret, values)
        self.assertEqual(frappe.db.get_value("Sale Return", ret.name, "refund_status"), "REFUNDED")
        self.assertEqual(before, (frappe.db.count("Stock Movement"), frappe.db.count("Credit Transaction"), self.balance()))

    def test_no_refund_cannot_be_completed(self):
        ret = self.returned(self.sale(qty=1), 1)
        with self.assertRaises(frappe.ValidationError):
            self.update(ret, self.completion())
        self.assertEqual(frappe.db.get_value("Sale Return", ret.name, "refund_status"), "NO_REFUND")

    def test_expired_owner_cannot_complete_refund(self):
        ret = self.pending()
        owner = self.make_user("OWNER")
        token = uuid4().hex[:10]
        plan = frappe.get_doc(dict(doctype="CreditFlow Plan", plan_name=token, plan_code="B08_" + token.upper(), enabled=1)).insert()
        frappe.get_doc(dict(doctype="CreditFlow Subscription", business=self.business.name, plan=plan.name, status="EXPIRED")).insert()
        clear_request_cache()
        frappe.set_user(owner)
        with self.assertRaises(frappe.PermissionError):
            self.update(ret, self.completion())
        self.assertEqual(frappe.db.get_value("Sale Return", ret.name, "refund_status"), "REFUND_DUE")

    def test_staff_and_foreign_owner_cannot_complete_refund(self):
        ret = self.pending()
        staff = self.make_user("STAFF")
        owner = self.make_user("OWNER")
        frappe.db.set_value("User", owner, "creditflow_business", self.other_business.name)
        for user in (staff, owner):
            frappe.set_user(user)
            with self.assertRaises(frappe.PermissionError):
                self.update(ret, self.completion())
        self.assertEqual(frappe.db.get_value("Sale Return", ret.name, "refund_status"), "REFUND_DUE")

    def test_submitted_economic_fields_and_items_remain_immutable(self):
        ret = self.pending()
        for values in ({"refund_amount": 1000}, {"business": self.other_business.name}, {"items": []}):
            with self.subTest(values=values):
                with self.assertRaises(frappe.ValidationError):
                    self.update(ret, values, "save")

    def test_stale_completion_payload_cannot_overwrite_completed_refund(self):
        ret = self.pending()
        stale = frappe.get_doc("Sale Return", ret.name).as_dict()
        self.update(ret, self.completion())
        stale.update(self.completion())
        stale.refund_reference = "second completion"
        with self.assertRaises(frappe.TimestampMismatchError):
            client.save(stale)
        self.assertEqual(frappe.db.get_value("Sale Return", ret.name, "refund_reference"), "B08")
