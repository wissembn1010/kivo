import frappe


from creditflow.i18n import get_language_context

def update_website_context(context):
    """Add Kivo language context and expose its controlled registration flow."""
    request = getattr(frappe.local, "request", None)
    if not request:
        return
    if request.path.rstrip("/") in {"/login", "/signup", "/check-email", "/verify-signup", "/creditflow-onboarding", "/creditflow-subscription", "/creditflow-payment-return", "/kivo-language"}:
        context.update(get_language_context())
    if request.path.rstrip("/") != "/login":
        return

    context.disable_signup = 0
    context.signup_form_template = frappe.get_template(
        "creditflow/templates/includes/kivo_login_signup.html"
    ).render(context)
