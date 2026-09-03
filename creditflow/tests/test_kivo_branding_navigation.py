import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from frappe.website.serve import get_response

import creditflow.hooks as app_hooks


class IntegrationTestKivoBrandingNavigation(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def render(self, path):
		frappe.set_user("Guest")
		set_request(method="GET", path=path)
		frappe.local.form_dict = frappe._dict(frappe.local.request.args)
		response = get_response(path)
		return response, frappe.safe_decode(response.get_data())

	def test_signup_route_uses_kivo_branding_and_keeps_sign_in(self):
		response, html = self.render("/signup")
		self.assertEqual(response.status_code, 200)
		self.assertIn("Start your Kivo trial", html)
		self.assertIn(">Kivo<", html)
		self.assertIn('href="/login"', html)
		self.assertNotIn(">CreditFlow<", html)

	def test_login_exposes_kivo_trial_without_enabling_generic_signup(self):
		response, html = self.render("/login")
		self.assertEqual(response.status_code, 200)
		self.assertIn('href="/signup"', html)
		self.assertIn("Start free trial", html)
		self.assertIn("Create your Kivo workspace", html)
		self.assertEqual(frappe.get_website_settings("disable_signup"), 1)

	def test_internal_app_identifier_remains_creditflow(self):
		workspace = frappe.get_doc("Workspace", "Kivo")
		self.assertEqual(app_hooks.app_name, "creditflow")
		self.assertEqual(workspace.name, "Kivo")
		self.assertEqual(workspace.app, "creditflow")
		self.assertEqual(workspace.title, "Kivo")
