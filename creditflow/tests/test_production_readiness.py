import frappe
from frappe.tests import IntegrationTestCase

from creditflow.billing.providers.konnect import KonnectProvider, PRODUCTION_BASE, SANDBOX_BASE
from creditflow.billing.readiness import get_konnect_readiness
from creditflow.signup import _verification_url


class IntegrationTestProductionReadiness(IntegrationTestCase):
    KEYS = ("creditflow_konnect_api_key", "creditflow_konnect_receiver_wallet_id", "creditflow_konnect_sandbox", "creditflow_public_base_url")

    def setUp(self):
        self.previous = {key: frappe.conf.get(key) for key in self.KEYS}

    def tearDown(self):
        for key, value in self.previous.items():
            if value is None:
                frappe.conf.pop(key, None)
            else:
                frappe.conf[key] = value

    def configure(self, sandbox):
        frappe.conf.creditflow_konnect_api_key = "never-return-this-secret"
        frappe.conf.creditflow_konnect_receiver_wallet_id = "wallet-test"
        frappe.conf.creditflow_konnect_sandbox = sandbox
        frappe.conf.creditflow_public_base_url = "https://creditflow.example"

    def test_readiness_is_secret_safe_and_reports_webhook(self):
        self.configure(1)
        result = get_konnect_readiness()
        self.assertTrue(result["ready"])
        self.assertTrue(result["api_key_configured"])
        self.assertNotIn("never-return-this-secret", repr(result))
        self.assertEqual(result["webhook_url"], "https://creditflow.example/api/method/creditflow.billing.api.konnect_webhook")

    def test_missing_configuration_is_reported_without_secret_values(self):
        for key in self.KEYS:
            frappe.conf.pop(key, None)
        result = get_konnect_readiness()
        self.assertFalse(result["ready"])
        self.assertEqual(len(result["issues"]), 3)
        self.assertIsNone(result["webhook_url"])

    def test_sandbox_and_production_endpoint_selection(self):
        self.configure(1)
        self.assertEqual(KonnectProvider().base_url, SANDBOX_BASE)
        self.configure("0")
        self.assertEqual(KonnectProvider().base_url, PRODUCTION_BASE)
        self.assertEqual(get_konnect_readiness()["mode"], "PRODUCTION")

    def test_verification_url_uses_central_public_origin(self):
        self.configure(1)
        self.assertEqual(_verification_url("opaque-token"), "https://creditflow.example/verify-signup?token=opaque-token")

    def test_non_https_public_origin_rejected_outside_developer_mode(self):
        self.configure(1)
        frappe.conf.creditflow_public_base_url = "http://creditflow.example"
        previous = frappe.conf.get("developer_mode")
        frappe.conf.developer_mode = 0
        try:
            with self.assertRaises(frappe.ValidationError):
                _verification_url("opaque-token")
        finally:
            frappe.conf.developer_mode = previous
