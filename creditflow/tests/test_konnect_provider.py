from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.billing.providers.konnect import KonnectProvider


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

    @patch("frappe.utils.get_request_session")
    def test_retired_checkout_fails_before_network_or_payload_processing(self, session):
        # No customer or payment payload should be inspected by a retired flow.
        with self.assertRaises(frappe.PermissionError):
            KonnectProvider().create_checkout(None, None)
        session.assert_not_called()

    @patch("frappe.utils.get_request_session")
    def test_retired_status_lookup_never_calls_network(self, session):
        with self.assertRaises(frappe.PermissionError):
            KonnectProvider().get_payment_status("historical-ref")
        session.assert_not_called()

    @patch("frappe.utils.get_request_session")
    def test_direct_retired_transport_cannot_bypass_entry_points(self, session):
        provider = KonnectProvider()
        for method, path in (("POST", "/payments/init-payment"), ("GET", "/payments/historical-ref")):
            with self.subTest(method=method):
                with self.assertRaises(frappe.PermissionError):
                    provider._request(method, path)
        session.assert_not_called()

    @patch("frappe.utils.get_request_session")
    def test_missing_gateway_configuration_also_fails_without_network(self, session):
        frappe.conf.pop("creditflow_konnect_api_key", None)
        with self.assertRaises(frappe.ValidationError):
            KonnectProvider()
        session.assert_not_called()
