from urllib.parse import urlencode
from uuid import uuid4

import frappe
from frappe.handler import execute_cmd
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from frappe.website.serve import get_response


ONBOARDING_CMD = "creditflow.saas_onboarding.complete_business_profile"


class IntegrationTestOnboardingWebsite(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.token = uuid4().hex[:12]
        self.business_a = self.create_business("Own Business", phone="111111", address="Own address")
        self.business_b = self.create_business("Other Tenant Secret", phone="222222", address="Other address")
        self.owner_a = self.create_owner("owner-a", self.business_a.name)
        self.owner_b = self.create_owner("owner-b", self.business_b.name)
        self.unassigned_owner = self.create_owner("unassigned", None)

    def tearDown(self):
        frappe.set_user("Administrator")

    def create_business(self, label, **values):
        return frappe.get_doc(
            {
                "doctype": "Business",
                "business_name": f"{label} {self.token}",
                "email": f"{label.lower().replace(' ', '-')}-{self.token}@example.com",
                "onboarding_status": "PROFILE_PENDING",
                **values,
            }
        ).insert(ignore_permissions=True)

    def create_owner(self, prefix, business):
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": f"onboarding-{prefix}-{self.token}@example.com",
                "first_name": "Onboarding",
                "send_welcome_email": 0,
                "creditflow_business": business,
            }
        ).insert(ignore_permissions=True)
        user.add_roles("OWNER")
        return user.name

    def render(self, user, query=None):
        frappe.set_user(user)
        query_string = urlencode(query or {})
        set_request(method="GET", path="/creditflow-onboarding", query_string=query_string)
        frappe.local.form_dict = frappe._dict(frappe.local.request.args)
        response = get_response("/creditflow-onboarding")
        return response, frappe.safe_decode(response.get_data())

    def rpc(self, user, **fields):
        frappe.set_user(user)
        frappe.local.form_dict = frappe._dict(cmd=ONBOARDING_CMD, **fields)
        set_request(method="POST", path=f"/api/method/{ONBOARDING_CMD}")
        return execute_cmd(ONBOARDING_CMD)

    def test_owner_get_renders_own_business_context(self):
        response, html = self.render(self.owner_a)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.business_a.business_name, html)
        self.assertIn(self.business_a.phone, html)
        self.assertIn(self.business_a.address, html)
        self.assertIn("WELCOME TO KIVO", html)
        self.assertIn('id="cf-profile-form"', html)
        self.assertNotIn("UndefinedError", html)

    def test_guest_cannot_access_tenant_details(self):
        response, html = self.render("Guest", {"business": self.business_a.name})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(self.business_a.business_name, html)
        self.assertNotIn(self.business_a.address, html)
        self.assertNotIn("UndefinedError", html)

    def test_unassigned_owner_get_is_safe(self):
        response, html = self.render(self.unassigned_owner)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('id="cf-profile-form"', html)
        self.assertNotIn("UndefinedError", html)

    def test_query_business_id_cannot_select_another_tenant(self):
        response, html = self.render(self.owner_a, {"business": self.business_b.name})
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.business_a.business_name, html)
        self.assertNotIn(self.business_b.business_name, html)
        self.assertNotIn(self.business_b.address, html)

    def test_complete_setup_realistic_rpc_updates_own_business(self):
        result = self.rpc(
            self.owner_a,
            business_name="Completed Legal Name",
            phone="+21620000000",
            country="Tunisia",
            address="Tunis",
            tax_identifier="MF-123",
            commercial_registration="RC-456",
        )
        self.business_a.reload()
        self.business_b.reload()
        self.assertEqual(result, {"ok": True, "redirect_to": "/app/kivo"})
        self.assertEqual(self.business_a.business_name, "Completed Legal Name")
        self.assertEqual(self.business_a.onboarding_status, "COMPLETED")
        self.assertTrue(self.business_a.onboarding_completed_at)
        self.assertEqual(self.business_b.onboarding_status, "PROFILE_PENDING")

    def test_skip_realistic_rpc_preserves_optional_values(self):
        before = self.business_a.as_dict()
        result = self.rpc(self.owner_a, skip=1)
        self.business_a.reload()
        self.assertEqual(result, {"ok": True, "redirect_to": "/app/kivo"})
        self.assertEqual(self.business_a.onboarding_status, "COMPLETED")
        for field in ("business_name", "phone", "country", "address", "tax_identifier", "commercial_registration"):
            self.assertEqual(self.business_a.get(field), before.get(field))

    def test_rpc_rejects_business_override(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Unsupported onboarding fields"):
            self.rpc(self.owner_a, skip=1, business=self.business_b.name)
        self.business_a.reload()
        self.business_b.reload()
        self.assertEqual(self.business_a.onboarding_status, "PROFILE_PENDING")
        self.assertEqual(self.business_b.onboarding_status, "PROFILE_PENDING")
