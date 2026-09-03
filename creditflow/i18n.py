from __future__ import annotations

import frappe
from frappe import _


SUPPORTED_LANGUAGES = {
	"en": "English",
	"fr": "Français",
	"ar": "العربية",
	"ar-TN": "تونسي",
}
RTL_LANGUAGES = {"ar", "ar-TN"}


def normalize_language(language: str | None) -> str:
	return language if language in SUPPORTED_LANGUAGES else "en"


def get_language_context() -> dict:
	language = normalize_language(getattr(frappe.local, "lang", None))
	return {
		"kivo_language": language,
		"kivo_languages": SUPPORTED_LANGUAGES,
		"kivo_direction": "rtl" if language in RTL_LANGUAGES else "ltr",
	}


@frappe.whitelist(allow_guest=True)
def set_language(language: str):
	if language not in SUPPORTED_LANGUAGES:
		frappe.throw(_("Unsupported language."))

	if frappe.session.user != "Guest":
		frappe.db.set_value("User", frappe.session.user, "language", language, update_modified=False)
		frappe.clear_cache(user=frappe.session.user)

	frappe.local.lang = language
	if not hasattr(frappe.local, "cookie_manager"):
		return {"language": language, "direction": "rtl" if language in RTL_LANGUAGES else "ltr"}
	frappe.local.cookie_manager.set_cookie(
		"preferred_language",
		language,
		max_age=60 * 60 * 24 * 365,
		samesite="Lax",
	)
	return {"language": language, "direction": "rtl" if language in RTL_LANGUAGES else "ltr"}
