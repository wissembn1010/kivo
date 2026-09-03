from urllib.parse import urlencode
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime, set_request
from frappe.website.serve import get_response

import creditflow.signup as signup_service


class IntegrationTestVerificationWebsite(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Guest")
        self.email = f"verification-page-{uuid4().hex[:12]}@example.com"
        self.tokens = []
        self.mail_patch = patch(
            "creditflow.signup._send_verification_email",
            side_effect=lambda email, token, language="en": self.tokens.append(token),
        )
        self.mail_patch.start()

    def tearDown(self):
        self.mail_patch.stop()
        frappe.set_user("Administrator")

    def create_pending(self):
        signup_service.request_signup(
            first_name="Amina",
            last_name="Trabelsi",
            email=self.email,
            business_name="Verification Route Test",
        )
        return frappe.get_doc("CreditFlow Pending Signup", {"email": self.email}), self.tokens[-1]

    def render(self, token=None):
        query_string = urlencode({"token": token}) if token is not None else ""
        set_request(method="GET", path="/verify-signup", query_string=query_string)
        frappe.local.form_dict = frappe._dict(frappe.local.request.args)
        response = get_response("/verify-signup")
        return response, frappe.safe_decode(response.get_data())

    def assert_invalid_page(self, token=None):
        response, html = self.render(token)
        self.assertEqual(response.status_code, 200)
        self.assertIn("This link is no longer valid", html)
        self.assertNotIn('id="cf-verify-form"', html)
        self.assertNotIn("UndefinedError", html)

    def test_valid_token_renders_password_form(self):
        pending, token = self.create_pending()
        response, html = self.render(token)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="cf-verify-form"', html)
        self.assertIn('id="cf-password-confirm"', html)
        self.assertIn(f'data-token="{token}"', html)
        self.assertIn("Create my Kivo workspace", html)
        self.assertNotIn(pending.token_hash, html)

    def test_missing_token_renders_invalid_state(self):
        self.assert_invalid_page()

    def test_unknown_and_malformed_tokens_render_invalid_state(self):
        self.assert_invalid_page("unknown-token")
        self.assert_invalid_page("x" * 257)

    def test_expired_token_renders_invalid_state(self):
        pending, token = self.create_pending()
        frappe.db.set_value(
            "CreditFlow Pending Signup",
            pending.name,
            "token_expires_at",
            add_to_date(now_datetime(), minutes=-1),
        )
        self.assert_invalid_page(token)

    def test_consumed_token_renders_login_option(self):
        pending, token = self.create_pending()
        frappe.db.set_value(
            "CreditFlow Pending Signup",
            pending.name,
            {
                "status": "PROVISIONED",
                "token_hash": None,
                "consumed_token_hash": signup_service._token_hash(token),
            },
        )
        response, html = self.render(token)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Email already verified", html)
        self.assertIn('href="/login?redirect-to=/creditflow-onboarding"', html)
        self.assertNotIn('id="cf-verify-form"', html)
