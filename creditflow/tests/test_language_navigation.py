from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from frappe.website.serve import get_response


class IntegrationTestKivoLanguageNavigation(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.lang = "en"

	def test_authenticated_home_and_back_links_return_to_kivo(self):
		email = f"language-navigation-{uuid4().hex[:10]}@example.com"
		frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": "Language Navigation",
			"send_welcome_email": 0,
		}).insert(ignore_permissions=True)
		frappe.set_user(email)
		set_request(method="GET", path="/kivo-language", query_string="_lang=fr")
		frappe.local.form_dict = frappe._dict(frappe.local.request.args)
		frappe.local.lang = "fr"

		response = get_response("/kivo-language")
		html = frappe.safe_decode(response.get_data())

		self.assertEqual(response.status_code, 200)
		self.assertIn('<a class="navbar-brand" href="/desk/kivo">', html)
		self.assertIn('<span>Accueil</span>', html)
		self.assertIn(
			'class="cf-primary cf-button-link" href="/desk/kivo">Retour à Kivo</a>', html
		)
		self.assertEqual(frappe.db.get_value("User", email, "language"), "en")

	def test_guest_home_and_back_links_return_to_login(self):
		frappe.set_user("Guest")
		set_request(method="GET", path="/kivo-language", query_string="_lang=ar-TN")
		frappe.local.form_dict = frappe._dict(frappe.local.request.args)
		frappe.local.lang = "ar-TN"

		response = get_response("/kivo-language")
		html = frappe.safe_decode(response.get_data())

		self.assertEqual(response.status_code, 200)
		self.assertIn('<a class="navbar-brand" href="/login">', html)
		self.assertIn('<span>الرئيسية</span>', html)
		self.assertIn(
			'class="cf-primary cf-button-link" href="/login">ارجع لـ Kivo</a>', html
		)
		self.assertIn('<option value="ar-TN" selected>', html)
