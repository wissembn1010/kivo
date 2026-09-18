from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from frappe.website.serve import get_response
from werkzeug.wrappers import Response

import creditflow.hooks as app_hooks
from creditflow.website import normalize_kivo_public_response


class IntegrationTestKivoBrandingNavigation(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		token = uuid4().hex[:10]
		business = frappe.get_doc({"doctype": "Business", "business_name": f"Branding {token}"}).insert()
		self.owner = f"branding-owner-{token}@example.com"
		user = frappe.get_doc({
			"doctype": "User", "email": self.owner, "first_name": "Branding Owner",
			"send_welcome_email": 0, "user_type": "System User",
			"creditflow_business": business.name, "default_workspace": "Kivo",
		}).insert(ignore_permissions=True)
		user.add_roles("OWNER")

	def tearDown(self):
		frappe.set_user("Administrator")

	def render(self, path):
		frappe.set_user("Guest")
		set_request(method="GET", path=path)
		frappe.local.form_dict = frappe._dict(frappe.local.request.args)
		response = get_response(path)
		normalize_kivo_public_response(response=response, request=frappe.local.request)
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
		self.assertNotIn('href="#signup"', html)
		self.assertIn("Start free trial", html)
		self.assertIn("Create your Kivo workspace", html)
		self.assertEqual(frappe.get_website_settings("disable_signup"), 1)

	def test_guest_root_uses_same_controlled_kivo_login_entry(self):
		response, html = self.render("/")
		self.assertEqual(response.status_code, 200)
		self.assertIn('href="/signup"', html)
		self.assertNotIn('href="#signup"', html)
		self.assertIn("Start free trial", html)
		self.assertIn("Create your Kivo workspace", html)
		self.assertEqual(frappe.get_website_settings("disable_signup"), 1)

	def test_authenticated_root_loads_kivo_desk(self):
		owner = self.owner
		self.assertTrue(frappe.db.exists("User", owner))
		frappe.set_user(owner)
		set_request(method="GET", path="/")
		frappe.local.form_dict = frappe._dict(frappe.local.request.args)
		response = get_response("/")
		html = frappe.safe_decode(response.get_data())
		self.assertEqual(response.status_code, 200)
		self.assertIn("frappe.boot", html)
		self.assertIn("Kivo", html)

	def test_owner_login_response_targets_kivo(self):
		owner = self.owner
		frappe.set_user(owner)
		response = Response(
			frappe.as_json({"message": "Logged In", "home_page": "/app/kivo"}),
			content_type="application/json",
		)
		normalize_kivo_public_response(
			response=response,
			request=frappe._dict(method="POST", path="/api/method/login"),
		)
		self.assertEqual(response.get_json()["home_page"], "/desk/kivo")

	def test_internal_app_identifier_remains_creditflow(self):
		workspace = frappe.get_doc("Workspace", "Kivo")
		self.assertEqual(app_hooks.app_name, "creditflow")
		self.assertEqual(workspace.name, "Kivo")
		self.assertEqual(workspace.app, "creditflow")
		self.assertEqual(workspace.title, "Kivo")

	def test_legacy_app_route_redirects_to_canonical_desk_route(self):
		frappe.set_user(self.owner)
		set_request(method="GET", path="/app/kivo")
		response = get_response("/app/kivo")
		self.assertEqual(response.status_code, 301)
		self.assertEqual(response.headers["Location"], "/desk/kivo")
