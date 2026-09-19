from hashlib import sha256
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_months, now_datetime

from creditflow.tests.http_test_support import request, request_many
from creditflow.tests.test_billing_konnect import FakeKonnect

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
            business = frappe.get_doc({"doctype": "Business", "business_name": f"HTTP Billing {index} {self.token}"}).insert().name
            self.businesses.append(business)
            key, secret = uuid4().hex, uuid4().hex
            user = frappe.get_doc({"doctype": "User", "email": f"billing-http-{index}-{self.token}@example.com",
                "first_name": "Billing HTTP", "send_welcome_email": 0, "creditflow_business": business,
                "api_key": key, "api_secret": secret}).insert()
            user.add_roles("OWNER")
            self.users.append(user.name)
            self.headers.append({"Authorization": f"token {key}:{secret}"})
            subscription = frappe.get_doc({"doctype": "CreditFlow Subscription", "business": business,
                "plan": self.plan, "status": "TRIAL", "trial_start": now_datetime(),
                "trial_end": add_days(now_datetime(), 14)}).insert()
            self.subscriptions.append(subscription.name)
            payment = frappe.get_doc({"doctype": "CreditFlow Billing Payment", "business": business,
                "subscription": subscription.name, "plan": self.plan, "provider": "KONNECT",
                "internal_order_id": uuid4().hex, "provider_payment_id": uuid4().hex,
                "amount": 59, "currency": "TND", "billing_cycle": "MONTHLY", "status": "PENDING"}).insert()
            self.payments.append(payment)
        self.payment = self.payments[0]
        FakeKonnect.details = {}; FakeKonnect.outage = False
        self.provider = patch.dict("creditflow.billing.service.PROVIDERS", {"KONNECT": FakeKonnect})
        self.provider.start(); self.addCleanup(self.provider.stop)
        self.ip = f"198.18.{int(self.token[:2], 16)}.{int(self.token[2:4], 16)}"
        self.webhook_ips = {self.ip}
        frappe.db.commit()

    def cleanup_records(self):
        frappe.set_user("Administrator"); frappe.db.rollback()
        for ip in getattr(self, "webhook_ips", ()):
            key = frappe.cache.make_key(f"rl:creditflow.billing.api.konnect_webhook:{ip}") + b":3600"
            frappe.cache.delete(key)
        for payment in self.payments:
            frappe.cache.delete(frappe.cache.make_key("konnect-webhook:" + sha256(payment.provider_payment_id.encode()).hexdigest()))
        for doctype in ("CreditFlow Billing Payment", "CreditFlow Subscription"):
            if self.businesses: frappe.db.delete(doctype, {"business": ["in", self.businesses]})
        for user in self.users: frappe.delete_doc("User", user, force=True, ignore_permissions=True)
        for business in self.businesses: frappe.delete_doc("Business", business, force=True, ignore_permissions=True)
        if self.plan: frappe.delete_doc("CreditFlow Plan", self.plan, force=True, ignore_permissions=True)
        frappe.db.commit()

    def details(self, **changes):
        data = {"normalized_status": "SUCCEEDED", "provider_status": "completed", "amount_minor": 59000,
            "currency": "TND", "order_id": self.payment.internal_order_id, "receiver_wallet_id": "wallet-test",
            "transaction_success": True, "payment_id": self.payment.provider_payment_id}
        data.update(changes); FakeKonnect.details[self.payment.provider_payment_id] = data

    def webhook(self, ref=None, **kwargs):
        self.webhook_ips.add(self.ip)
        result = request(WEBHOOK + "?payment_ref=" + (ref or self.payment.provider_payment_id),
            method="GET", remote_addr=self.ip, **kwargs)
        frappe.db.rollback()
        return result

    def test_http_get_commits_verified_success_and_replay_is_idempotent(self):
        self.details()
        with patch.object(FakeKonnect, "get_payment_status", autospec=True,
                side_effect=lambda provider, ref: dict(FakeKonnect.details[ref])) as poll:
            status, body = self.webhook()
            self.assertEqual(status, 200)
            self.assertEqual(body, {"message": {"ok": True}})
            self.payment.reload()
            self.assertEqual(self.payment.status, "SUCCEEDED")
            end = frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "current_period_end")
            self.assertTrue(end)
            self.assertEqual(self.webhook(), (status, body))
            self.assertEqual(poll.call_count, 1)
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "current_period_end"), end)
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[1], "status"), "TRIAL")

    def test_http_unknown_invalid_and_pending_have_same_opaque_response(self):
        self.details(normalized_status="PENDING", provider_status="pending")
        for ref in (uuid4().hex, "bad!", "X" * 129, self.payment.provider_payment_id):
            self.assertEqual(self.webhook(ref), (200, {"message": {"ok": True}}))
        self.assertEqual(request(WEBHOOK, method="GET", remote_addr=self.ip), (200, {"message": {"ok": True}}))
        self.assertEqual(self.payment.reload().status, "PENDING")

    def test_http_browser_claims_cannot_activate_and_post_is_not_supported(self):
        self.details(normalized_status="PENDING", provider_status="pending")
        status, _ = self.webhook(payload={"status": "SUCCEEDED", "amount": 59000, "business": self.businesses[1]})
        self.assertEqual(status, 200)
        self.assertEqual(self.payment.reload().status, "PENDING")
        status, _ = request(WEBHOOK, payload={"payment_ref": self.payment.provider_payment_id}, remote_addr=self.ip)
        self.assertIn(status, (403, 405))
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "status"), "TRIAL")

    def test_http_mismatched_or_malformed_provider_confirmation_cannot_activate(self):
        for changes in ({"payment_id": "wrong-reference"}, {"payment_id": None},
                {"receiver_wallet_id": None}, {"amount_minor": "59000.9"}, {"amount_minor": "bad"},
                {"currency": "USD"}, {"order_id": "wrong-order"}, {"transaction_success": False}):
            with self.subTest(changes=changes):
                self.details(**changes)
                self.assertEqual(self.webhook(), (200, {"message": {"ok": True}}))
                self.assertEqual(self.payment.reload().status, "PENDING")
                self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "status"), "TRIAL")

    def test_http_provider_outage_is_generic_and_does_not_activate(self):
        with patch.object(FakeKonnect, "get_payment_status", side_effect=RuntimeError("SECRET_API_KEY")):
            self.assertEqual(self.webhook(), (200, {"message": {"ok": True}}))
        self.payment.reload()
        self.assertEqual(self.payment.status, "PENDING")
        self.assertEqual(self.payment.reconciliation_count, 1)
        self.assertNotIn("SECRET_API_KEY", self.payment.as_json())

    def test_http_owner_refresh_still_works_and_rejects_other_tenant(self):
        self.details()
        path = "/api/method/creditflow.billing.api.refresh_payment"
        status, _ = request(path, payload={"payment": self.payment.name}, headers=self.headers[1])
        self.assertEqual(status, 403)
        status, body = request(path, payload={"payment": self.payment.name}, headers=self.headers[0])
        self.assertEqual(status, 200)
        self.assertEqual(body["message"], {"payment": self.payment.name, "status": "SUCCEEDED"})
        frappe.db.rollback()
        self.assertEqual(self.payment.reload().status, "SUCCEEDED")

    def test_http_failure_after_activation_rolls_back_payment_and_subscription(self):
        from creditflow.creditflow.doctype.creditflow_billing_payment.creditflow_billing_payment import CreditFlowBillingPayment
        original = CreditFlowBillingPayment.save
        self.details()
        def fail_success(doc, *args, **kwargs):
            if doc.status == "SUCCEEDED": raise RuntimeError("forced final payment failure")
            return original(doc, *args, **kwargs)
        with patch.object(CreditFlowBillingPayment, "save", fail_success):
            status, _ = self.webhook()
        self.assertEqual(status, 500)
        self.assertEqual(self.payment.reload().status, "PENDING")
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "status"), "TRIAL")

    def test_http_reference_budget_limits_provider_calls_across_ips(self):
        self.details(normalized_status="PENDING", provider_status="pending")
        with patch.object(FakeKonnect, "get_payment_status", autospec=True,
                side_effect=lambda provider, ref: dict(FakeKonnect.details[ref])) as poll:
            for index in range(11):
                self.ip = f"198.19.0.{index + 1}"
                status, body = self.webhook()
                self.assertEqual(status, 200 if index < 10 else 429)
            self.assertEqual(poll.call_count, 10)
        self.assertNotIn(self.payment.name, str(body))
        self.assertEqual(self.payment.reload().status, "PENDING")

    def test_concurrent_http_replays_apply_payment_once(self):
        from creditflow.billing.service import reconcile_provider_payment
        self.details()
        ready = Barrier(2)
        def together(*args, **kwargs):
            ready.wait(timeout=10)
            return reconcile_provider_payment(*args, **kwargs)
        call = {"path": WEBHOOK + "?payment_ref=" + self.payment.provider_payment_id,
            "method": "GET", "remote_addr": self.ip}
        with patch("creditflow.billing.api.reconcile_provider_payment", side_effect=together), patch.object(
                FakeKonnect, "get_payment_status", autospec=True,
                side_effect=lambda provider, ref: dict(FakeKonnect.details[ref])) as poll:
            results = request_many([call, call])
        self.assertEqual(results, [(200, {"message": {"ok": True}})] * 2)
        self.assertEqual(poll.call_count, 1)
        frappe.db.rollback(); self.payment.reload()
        self.assertEqual(self.payment.status, "SUCCEEDED")
        self.assertEqual(self.payment.billing_period_end, add_months(self.payment.billing_period_start, 1))

    def test_concurrent_distinct_payments_preserve_both_paid_periods(self):
        from creditflow.billing.service import reconcile_provider_payment
        self.details()
        second = frappe.copy_doc(self.payment)
        second.provider_payment_id = uuid4().hex; second.internal_order_id = uuid4().hex
        second.insert(); self.payments.append(second)
        FakeKonnect.details[second.provider_payment_id] = dict(FakeKonnect.details[self.payment.provider_payment_id],
            payment_id=second.provider_payment_id, order_id=second.internal_order_id)
        frappe.db.commit()
        ready = Barrier(2)
        def together(*args, **kwargs):
            ready.wait(timeout=10)
            return reconcile_provider_payment(*args, **kwargs)
        calls = [{"path": WEBHOOK + "?payment_ref=" + payment.provider_payment_id,
            "method": "GET", "remote_addr": self.ip} for payment in (self.payment, second)]
        with patch("creditflow.billing.api.reconcile_provider_payment", side_effect=together):
            results = request_many(calls)
        # MariaDB with innodb_snapshot_isolation may reject a concurrent renewal
        # against an old request snapshot. A rejected transaction must remain
        # pending and be safely retryable, never lose or double-apply paid time.
        self.assertTrue(any(status == 200 for status, _ in results))
        for payment, call, (status, body) in zip((self.payment, second), calls, results):
            if status != 200:
                self.assertEqual((status, body), (500, {"exc_type": "QueryDeadlockError"}))
                frappe.db.rollback()
                self.assertEqual(payment.reload().status, "PENDING")
                self.assertEqual(request_many([call])[0], (200, {"message": {"ok": True}}))
            else:
                self.assertEqual(body, {"message": {"ok": True}})
        frappe.db.rollback(); self.payment.reload(); second.reload()
        first, last = sorted((self.payment, second), key=lambda payment: payment.billing_period_start)
        self.assertEqual((first.status, last.status), ("SUCCEEDED", "SUCCEEDED"))
        self.assertEqual(first.billing_period_end, last.billing_period_start)
        self.assertEqual(last.billing_period_end, add_months(last.billing_period_start, 1))
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription", self.subscriptions[0], "current_period_end"), last.billing_period_end)

    def test_mislinked_historical_payment_cannot_activate_another_tenant(self):
        self.details()
        # Simulate a historical inconsistent link, not an allowed owner write.
        frappe.db.set_value("CreditFlow Billing Payment", self.payment.name, "subscription", self.subscriptions[1])
        frappe.db.commit()
        status, _ = self.webhook()
        self.assertEqual(status, 403)
        self.assertEqual(self.payment.reload().status, "PENDING")
        for subscription in self.subscriptions:
            self.assertEqual(frappe.db.get_value("CreditFlow Subscription", subscription, "status"), "TRIAL")

    def test_http_ip_limit_still_blocks_different_references(self):
        key = frappe.cache.make_key(f"rl:creditflow.billing.api.konnect_webhook:{self.ip}") + b":3600"
        # Seed only the real counter; the actual WSGI decorator enforces the limit.
        frappe.cache.setex(key, 3600, 119)
        self.assertEqual(self.webhook(uuid4().hex), (200, {"message": {"ok": True}}))
        status, body = self.webhook(uuid4().hex)
        self.assertEqual(status, 429)
        self.assertEqual(body["exc_type"], "RateLimitExceededError")
        self.assertEqual(self.payment.reload().reconciliation_count or 0, 0)
