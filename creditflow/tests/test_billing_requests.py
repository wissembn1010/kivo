"""Real HTTP authorization, rollback and concurrency for bank payments."""
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_months, now_datetime

from creditflow.tests.http_test_support import request, request_many

REVIEW = "/api/method/creditflow.billing.api.admin_review"
WEBHOOK = "/api/method/creditflow.billing.api.konnect_webhook"


class IntegrationTestBillingRequests(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.token = uuid4().hex
        self.businesses, self.users, self.payments, self.subscriptions = [], [], [], []
        self.plan = None
        self.addCleanup(self.cleanup_records)
        self.plan = frappe.get_doc({"doctype": "CreditFlow Plan", "plan_name": self.token,
            "plan_code": "HTTP_" + self.token.upper(), "enabled": 1, "monthly_price": 59}).insert().name
        self.headers = []
        for index in range(2):
            business = frappe.get_doc({"doctype": "Business", "business_name": f"HTTP Bank {index} {self.token}"}).insert().name
            self.businesses.append(business)
            self.headers.append(self.make_user(str(index), "OWNER", business))
            sub = frappe.get_doc({"doctype": "CreditFlow Subscription", "business": business,
                "plan": self.plan, "status": "TRIAL", "trial_start": add_days(now_datetime(), -15),
                "trial_end": add_days(now_datetime(), -1)}).insert()
            self.subscriptions.append(sub.name)
            payment = frappe.get_doc({"doctype": "CreditFlow Billing Payment", "business": business,
                "subscription": sub.name, "plan": self.plan, "provider": "BANK_TRANSFER",
                "internal_order_id": "CF-" + uuid4().hex, "amount": 59, "currency": "TND",
                "billing_cycle": "MONTHLY", "status": "PENDING_PAYMENT", "created_at": now_datetime(),
                "expires_at": add_days(now_datetime(), 7), "destination_account": "25190000000150858272"}).insert()
            self.payments.append(payment)
        self.admin_headers = self.make_user("admin", "System Manager")
        self.payment = self.payments[0]
        self.ip = f"198.18.{int(self.token[:2], 16)}.{int(self.token[2:4], 16)}"
        frappe.db.commit()

    def make_user(self, label, role, business=None):
        key, secret = uuid4().hex, uuid4().hex
        user = frappe.get_doc({"doctype": "User", "email": f"bank-http-{label}-{self.token}@example.com",
            "first_name": "Bank HTTP", "send_welcome_email": 0, "creditflow_business": business,
            "api_key": key, "api_secret": secret}).insert()
        user.add_roles(role); self.users.append(user.name)
        return {"Authorization": f"token {key}:{secret}"}

    def cleanup_records(self):
        frappe.set_user("Administrator"); frappe.db.rollback()
        for endpoint in ("admin_review", "submit_proof", "refresh_payment", "start_checkout", "konnect_webhook"):
            key = frappe.cache.make_key(f"rl:creditflow.billing.api.{endpoint}:{self.ip}") + b":3600"
            frappe.cache.delete(key)
        for doctype in ("CreditFlow Billing Audit", "CreditFlow Bank Transaction"):
            if frappe.db.table_exists(doctype):
                for payment in self.payments:
                    if frappe.get_meta(doctype).has_field("payment"):
                        frappe.db.delete(doctype, {"payment": payment.name})
        for payment in self.payments:
            for name in frappe.get_all("File", filters={"attached_to_doctype": payment.doctype,
                    "attached_to_name": payment.name}, pluck="name"):
                from creditflow.billing.bank import billing_mutation
                with billing_mutation():
                    frappe.delete_doc("File", name, ignore_permissions=True)
        for doctype in ("CreditFlow Billing Payment", "CreditFlow Subscription"):
            if self.businesses: frappe.db.delete(doctype, {"business": ["in", self.businesses]})
        for user in self.users: frappe.delete_doc("User", user, force=True, ignore_permissions=True)
        for business in self.businesses: frappe.delete_doc("Business", business, force=True, ignore_permissions=True)
        if self.plan: frappe.delete_doc("CreditFlow Plan", self.plan, force=True, ignore_permissions=True)
        frappe.db.commit()

    def payload(self, payment=None, **changes):
        payment = payment or self.payment
        return {"payment": payment.name, "action": "VERIFIED", "transaction_id": "HTTP-" + uuid4().hex,
            "reference": payment.internal_order_id, "amount": payment.amount,
            "transaction_date": now_datetime().date().isoformat(), "destination_account": payment.destination_account,
            "notes": "Actual received funds confirmed", **changes}

    def review(self, payload=None, headers=None):
        result = request(REVIEW, payload=payload or self.payload(),
            headers=headers or self.admin_headers, remote_addr=self.ip)
        frappe.db.rollback()
        return result

    def test_legacy_webhook_is_inert_for_every_customer_claim(self):
        for reference in (self.payment.internal_order_id, "fake", "X" * 129):
            status, body = request(WEBHOOK + "?payment_ref=" + reference, method="GET",
                payload={"status": "VERIFIED", "amount": 59}, remote_addr=self.ip)
            self.assertEqual((status, body), (200, {"message": {"ok": True}}))
        frappe.db.rollback()
        self.assertEqual(self.payment.reload().status, "PENDING_PAYMENT")
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "status"), "TRIAL")

    def test_browser_checkout_routing_command_is_allowed_but_price_override_denied(self):
        endpoint = "/api/method/creditflow.billing.api.start_checkout"
        with patch("creditflow.billing.service.is_paid_checkout_enabled", return_value=True):
            status, body = request(endpoint, payload={"cmd": "creditflow.billing.api.start_checkout",
                "plan": self.plan, "billing_cycle": "MONTHLY"}, headers=self.headers[0], remote_addr=self.ip)
            self.assertEqual(status, 200, body)
            frappe.db.rollback()
            payment = frappe.get_doc("CreditFlow Billing Payment", body["message"]["payment"])
            self.payments.append(payment)
            self.assertEqual(payment.status, "PENDING_PAYMENT")
            self.assertEqual(payment.amount, 59)
            status, _ = request(endpoint, payload={"cmd": "creditflow.billing.api.start_checkout",
                "plan": self.plan, "amount": 1}, headers=self.headers[0], remote_addr=self.ip)
            self.assertEqual(status, 417)

    def test_payment_page_controller_loads_payment_and_denies_foreign_or_guest_access(self):
        from frappe.utils import set_request
        from frappe.website.page_renderers.template_page import TemplatePage

        page = TemplatePage("creditflow-payment-return")
        page.set_pymodule()
        self.assertEqual(page.pymodule_name, "creditflow.www.creditflow_payment_return")
        frappe.set_user(self.users[0])
        set_request(method="GET", path="/creditflow-payment-return")
        frappe.local.form_dict = frappe._dict(payment=self.payment.name)
        html = page.get_html()
        self.assertIn(f'data-payment="{self.payment.name}"', html)
        path = "/creditflow-payment-return?payment=" + self.payment.name
        self.assertEqual(request(path, method="GET", headers=self.headers[1])[0], 403)
        self.assertEqual(request(path, method="GET")[0], 403)

    def test_guest_owner_other_tenant_cannot_approve(self):
        self.assertEqual(request(REVIEW, payload=self.payload(), remote_addr=self.ip)[0], 403)
        for headers in self.headers:
            self.assertEqual(self.review(headers=headers)[0], 403)
        self.assertEqual(self.payment.reload().status, "PENDING_PAYMENT")

    def test_owner_status_is_read_only_and_tenant_scoped(self):
        path = "/api/method/creditflow.billing.api.refresh_payment"
        self.assertEqual(request(path, payload={"payment": self.payment.name}, headers=self.headers[1])[0], 403)
        status, body = request(path, payload={"payment": self.payment.name}, headers=self.headers[0])
        self.assertEqual(status, 200)
        self.assertEqual(body["message"]["status"], "PENDING_PAYMENT")
        frappe.db.rollback()
        self.assertEqual(self.payment.reload().status, "PENDING_PAYMENT")

    def test_http_verified_approval_commits_and_replay_is_idempotent(self):
        payload = self.payload()
        self.assertEqual(self.review(payload)[0], 200)
        self.assertEqual(self.payment.reload().status, "VERIFIED")
        end = frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "current_period_end")
        self.assertTrue(end)
        self.assertEqual(self.review(payload)[0], 200)
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "current_period_end"), end)
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[1], "status"), "TRIAL")

    def test_failure_after_activation_rolls_back_both_records(self):
        from creditflow.creditflow.doctype.creditflow_billing_payment.creditflow_billing_payment import CreditFlowBillingPayment
        original = CreditFlowBillingPayment.save
        def fail_verified(doc, *args, **kwargs):
            if doc.status == "VERIFIED":
                raise RuntimeError("forced final payment failure")
            return original(doc, *args, **kwargs)
        with patch.object(CreditFlowBillingPayment, "save", fail_verified):
            self.assertEqual(self.review()[0], 500)
        self.assertEqual(self.payment.reload().status, "PENDING_PAYMENT")
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "status"), "TRIAL")
        self.assertEqual(self.review()[0], 200)
        self.assertEqual(self.payment.reload().status, "VERIFIED")

    def test_simultaneous_approval_applies_once(self):
        from creditflow.billing.bank import admin_review
        ready = Barrier(2)
        def together(*args, **kwargs):
            ready.wait(timeout=10)
            return admin_review(*args, **kwargs)
        call = {"path": REVIEW, "payload": self.payload(), "headers": self.admin_headers, "remote_addr": self.ip}
        with patch("creditflow.billing.api.bank.admin_review", side_effect=together):
            results = request_many([call, call])
        self.assertTrue(any(status == 200 for status, _ in results))
        for status, body in results:
            if status != 200:
                self.assertEqual(status, 500)
                self.assertEqual(body["exc_type"], "QueryDeadlockError")
                self.assertEqual(request_many([call])[0][0], 200)
        frappe.db.rollback(); self.payment.reload()
        self.assertEqual(self.payment.status, "VERIFIED")
        self.assertEqual(self.payment.billing_period_end, add_months(self.payment.billing_period_start, 1))

    def test_concurrent_distinct_transfers_preserve_both_periods(self):
        from creditflow.billing.bank import admin_review
        second = frappe.copy_doc(self.payment)
        second.internal_order_id = "CF-" + uuid4().hex
        second.insert(); self.payments.append(second); frappe.db.commit()
        ready = Barrier(2)
        def together(*args, **kwargs):
            ready.wait(timeout=10)
            return admin_review(*args, **kwargs)
        calls = [{"path": REVIEW, "payload": self.payload(payment), "headers": self.admin_headers,
            "remote_addr": self.ip} for payment in (self.payment, second)]
        with patch("creditflow.billing.api.bank.admin_review", side_effect=together):
            results = request_many(calls)
        self.assertTrue(any(status == 200 for status, _ in results))
        for call, (status, body) in zip(calls, results):
            if status != 200:
                self.assertEqual(status, 500)
                self.assertEqual(body["exc_type"], "QueryDeadlockError")
                self.assertEqual(request_many([call])[0][0], 200)
        frappe.db.rollback(); self.payment.reload(); second.reload()
        first, last = sorted((self.payment, second), key=lambda item: item.billing_period_start)
        self.assertEqual((first.status, last.status), ("VERIFIED", "VERIFIED"))
        self.assertEqual(first.billing_period_end, last.billing_period_start)
        self.assertEqual(last.billing_period_end, add_months(last.billing_period_start, 1))
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "current_period_end"), last.billing_period_end)

    def test_mislinked_historical_payment_cannot_credit_other_tenant(self):
        frappe.db.set_value("CreditFlow Billing Payment", self.payment.name, "subscription", self.subscriptions[1])
        frappe.db.commit()
        self.assertEqual(self.review()[0], 403)
        self.assertEqual(self.payment.reload().status, "PENDING_PAYMENT")
        for sub in self.subscriptions:
            self.assertEqual(frappe.db.get_value("CreditFlow Subscription", sub, "status"), "TRIAL")

    def test_receipt_upload_private_storage_and_download_authorization(self):
        from creditflow.tests.test_billing_konnect import pdf_receipt
        content = pdf_receipt()
        path = "/api/method/creditflow.billing.api.submit_proof"
        payload = {"payment": self.payment.name, "filename": "proof.pdf", "content": content}
        self.assertEqual(request(path, payload=payload, headers=self.headers[1], remote_addr=self.ip)[0], 403)
        self.assertEqual(request(path, payload=payload, headers=self.headers[0], remote_addr=self.ip)[0], 200)
        frappe.db.rollback(); self.payment.reload()
        self.assertEqual(self.payment.status, "PROOF_SUBMITTED")
        self.assertTrue(self.payment.receipt.startswith("/private/files/"))
        self.assertEqual(request(self.payment.receipt, method="GET", headers=self.headers[1], remote_addr=self.ip)[0], 403)
        self.assertEqual(request(self.payment.receipt, method="GET", remote_addr=self.ip)[0], 403)
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "status"), "TRIAL")

    def test_rest_resource_updates_cannot_forge_paid_access(self):
        for doctype, name, changes in (("CreditFlow Billing Payment", self.payment.name, {"status": "VERIFIED"}),
                ("CreditFlow Subscription", self.subscriptions[0], {"status": "ACTIVE"})):
            path = f"/api/resource/{doctype}/{name}"
            self.assertEqual(request(path, method="PUT", payload=changes, headers=self.headers[0], remote_addr=self.ip)[0], 403)
        frappe.db.rollback()
        self.assertEqual(self.payment.reload().status, "PENDING_PAYMENT")
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "status"), "TRIAL")

    def test_review_rate_limit_blocks_excess_approval(self):
        key = frappe.cache.make_key(f"rl:creditflow.billing.api.admin_review:{self.ip}") + b":3600"
        frappe.cache.setex(key, 3600, 60)
        status, body = self.review()
        self.assertEqual(status, 429)
        self.assertEqual(body["exc_type"], "RateLimitExceededError")
        self.assertEqual(self.payment.reload().status, "PENDING_PAYMENT")
