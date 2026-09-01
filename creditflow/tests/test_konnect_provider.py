from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.billing.providers.konnect import KonnectProvider, SANDBOX_BASE


class IntegrationTestKonnectProviderPayloads(IntegrationTestCase):
    def setUp(self):
        self.old = {key: frappe.conf.get(key) for key in (
            "creditflow_konnect_api_key", "creditflow_konnect_receiver_wallet_id",
            "creditflow_konnect_sandbox", "creditflow_public_base_url")}
        frappe.conf.creditflow_konnect_api_key = "test-only-key"
        frappe.conf.creditflow_konnect_receiver_wallet_id = "wallet-test"
        frappe.conf.creditflow_konnect_sandbox = True
        frappe.conf.creditflow_public_base_url = "https://creditflow.example"

    def tearDown(self):
        for key, value in self.old.items():
            if value is None:
                frappe.conf.pop(key, None)
            else:
                frappe.conf[key] = value

    @patch("creditflow.billing.providers.konnect.make_post_request")
    def test_documented_checkout_payload_and_response(self, request):
        request.return_value = {"payUrl": "https://sandbox.konnect.network/pay/abc", "paymentRef": "ref12345678"}
        payment = SimpleNamespace(currency="TND", amount_minor=59500, plan="PRO", billing_cycle="MONTHLY", internal_order_id="CF-order123")
        result = KonnectProvider().create_checkout(payment, {"first_name":"A", "last_name":"Owner", "email":"a@example.com", "phone":"20000000"})
        self.assertEqual(result["provider_payment_id"], "ref12345678")
        args, kwargs = request.call_args
        self.assertEqual(args[0], f"{SANDBOX_BASE}/payments/init-payment")
        self.assertEqual(kwargs["json"]["amount"], 59500)
        self.assertEqual(kwargs["json"]["orderId"], "CF-order123")
        self.assertNotIn("creditflow_konnect_api_key", kwargs["json"])

    @patch("creditflow.billing.providers.konnect.make_get_request")
    def test_documented_payment_details_are_normalized(self, request):
        request.return_value = {"payment": {"id":"ref12345678", "status":"completed", "reachedAmount":59500,
            "token":"TND", "orderId":"CF-order123", "receiverWallet":{"id":"wallet-test"},
            "transactions":[{"status":"success"}]}}
        result = KonnectProvider().get_payment_status("ref12345678")
        self.assertEqual(result["normalized_status"], "SUCCEEDED")
        self.assertEqual(result["amount_minor"], 59500)
        self.assertTrue(result["transaction_success"])
