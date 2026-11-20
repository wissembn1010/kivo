from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader, StrictUndefined, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

from creditflow import hooks, i18n
from creditflow.www import creditflow_onboarding


class TestWebsiteLanguageSelector(TestCase):
	def setUp(self):
		app = Path(i18n.__file__).parent
		self.env = SandboxedEnvironment(
			undefined=StrictUndefined,
			loader=ChoiceLoader([
				DictLoader({"templates/web.html": "{% block page_content %}{% endblock %}"}),
				FileSystemLoader(app.parent),
			]),
		)
		self.env.globals["_"] = str
		for method in hooks.jinja["methods"]:
			module, name = method.rsplit(".", 1)
			self.env.globals[name] = getattr(import_module(module), name)
		self.business = dict.fromkeys(
			("business_name", "phone", "country", "address", "tax_identifier", "commercial_registration"), ""
		)

	def assert_selector(self, html, language):
		for code, label in i18n.SUPPORTED_LANGUAGES.items():
			selected = " selected" if code == language else ""
			self.assertIn(f'<option value="{code}"{selected}>{label}</option>', html)
		self.assertIn(f'const language = "{language}";', html)
		direction = "rtl" if language in i18n.RTL_LANGUAGES else "ltr"
		self.assertIn(f'const direction = "{direction}";', html)

	def test_onboarding_controller_supplies_context(self):
		ctx = frappe._dict()
		with (
			patch.object(creditflow_onboarding, "_", str),
			patch.object(creditflow_onboarding, "get_onboarding_business", return_value=self.business),
			patch.object(i18n.frappe, "local", SimpleNamespace(lang="fr")),
		):
			creditflow_onboarding.get_context(ctx)
		self.assertEqual(ctx.kivo_languages, i18n.SUPPORTED_LANGUAGES)
		self.env.globals.pop("get_language_context")
		self.assert_selector(self.env.get_template("creditflow/www/creditflow-onboarding.html").render(ctx), "fr")

	def test_pages_without_website_hook(self):
		pages = {
			"www/creditflow-onboarding.html": {"business": self.business},
			"www/check-email.html": {},
			"www/signup.html": {"countries": []},
			"www/verify-signup.html": {"verification": {"valid": True}, "token": "test"},
			"www/kivo-language.html": {"kivo_return_url": "/login"},
			"templates/includes/kivo_login_signup.html": {},
		}
		for language in ("en", "fr", "ar", "ar-TN"):
			with patch.object(i18n.frappe, "local", SimpleNamespace(lang=language)):
				for page, context in pages.items():
					with self.subTest(page=page, language=language):
						html = self.env.get_template(f"creditflow/{page}").render(context)
						self.assert_selector(html, language)

	def test_partial_context_uses_direction_of_explicit_language(self):
		with patch.object(i18n.frappe, "local", SimpleNamespace(lang="en")):
			html = self.env.get_template("creditflow/www/creditflow-onboarding.html").render(
				business=self.business, kivo_language="ar-TN"
			)
		self.assert_selector(html, "ar-TN")

	def test_invalid_explicit_mapping_surfaces(self):
		template = self.env.get_template("creditflow/templates/includes/kivo_language_selector.html")
		with self.assertRaises(UndefinedError):
			template.render(kivo_languages=None, kivo_language="en", kivo_direction="ltr")
