from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from frappe.website.serve import get_response

from creditflow.i18n import RTL_LANGUAGES, SUPPORTED_LANGUAGES, set_language


class IntegrationTestKivoLocalization(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.lang = "en"

	def test_supported_languages_and_tunisian_record(self):
		self.assertEqual(
			SUPPORTED_LANGUAGES,
			{"en": "English", "fr": "Français", "ar": "العربية", "ar-TN": "تونسي"},
		)
		language = frappe.get_doc("Language", "ar-TN")
		self.assertEqual(language.based_on, "ar")
		self.assertEqual(language.number_format, "#,###.###")
		self.assertEqual(RTL_LANGUAGES, {"ar", "ar-TN"})

	def test_french_arabic_and_derja_are_distinct(self):
		self.assertEqual(frappe._("New Sale", lang="fr"), "Nouvelle vente")
		self.assertEqual(frappe._("New Sale", lang="ar"), "عملية بيع جديدة")
		self.assertEqual(frappe._("New Sale", lang="ar-TN"), "بيعة جديدة")
		self.assertEqual(frappe._("Customers", lang="ar"), "العملاء")
		self.assertEqual(frappe._("Customers", lang="ar-TN"), "الحرفاء")
		self.assertEqual(frappe._("Missing Kivo translation", lang="ar-TN"), "Missing Kivo translation")

	def test_public_signup_uses_selected_language_and_direction(self):
		for language, text, direction in (
			("en", "Start your free trial", "ltr"),
			("fr", "Démarrez votre essai gratuit", "ltr"),
			("ar", "ابدأ تجربتك المجانية", "rtl"),
			("ar-TN", "ابدأ التجربة المجانية", "rtl"),
		):
			frappe.set_user("Guest")
			set_request(method="GET", path="/signup", query_string=f"_lang={language}")
			frappe.local.form_dict = frappe._dict(frappe.local.request.args)
			frappe.local.lang = language
			response = get_response("/signup")
			html = frappe.safe_decode(response.get_data())
			self.assertEqual(response.status_code, 200)
			self.assertIn(text, html)
			self.assertIn(f'const direction = "{direction}"', html)
			self.assertIn(f'<option value="{language}" selected>', html)

	def test_authenticated_language_persists_on_user(self):
		email = f"language-{uuid4().hex[:10]}@example.com"
		frappe.get_doc({
			"doctype": "User", "email": email, "first_name": "Language",
			"send_welcome_email": 0,
		}).insert(ignore_permissions=True)
		frappe.set_user(email)
		result = set_language("ar-TN")
		self.assertEqual(result, {"language": "ar-TN", "direction": "rtl"})
		self.assertEqual(frappe.db.get_value("User", email, "language"), "ar-TN")

	def test_internal_identifiers_and_tej_codes_are_not_translated(self):
		self.assertEqual(frappe.get_doc("Workspace", "Kivo").app, "creditflow")
		self.assertEqual(frappe._("creditflow", lang="ar-TN"), "creditflow")
		self.assertEqual(frappe._("TEJ", lang="ar-TN"), "TEJ")
