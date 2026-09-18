import frappe


from creditflow.i18n import get_language_context

def update_website_context(context):
    """Add Kivo language context and expose its controlled registration flow."""
    request = getattr(frappe.local, "request", None)
    if not request:
        return
    request_path = request.path.rstrip("/")
    is_guest_login = request_path == "/login" or (
        not request_path and frappe.session.user == "Guest"
    )
    if is_guest_login or request_path in {"/signup", "/check-email", "/verify-signup", "/creditflow-onboarding", "/creditflow-subscription", "/creditflow-payment-return", "/kivo-language"}:
        context.update(get_language_context())
    if not is_guest_login:
        return

    context.disable_signup = 0
    context.signup_form_template = frappe.get_template(
        "creditflow/templates/includes/kivo_login_signup.html"
    ).render(context)


def normalize_kivo_public_response(response, request):
    """Keep public login links and successful OWNER redirects inside Kivo."""
    if request.method == "POST" and request.path == "/api/method/login":
        if frappe.session.user != "Guest" and "OWNER" in frappe.get_roles():
            payload = response.get_json(silent=True)
            if payload and payload.get("message") == "Logged In":
                payload["home_page"] = "/desk/kivo"
                response.set_data(frappe.as_json(payload))
        return

    if request.method != "GET" or request.path.rstrip("/") not in {"", "/login"}:
        return
    if frappe.session.user != "Guest" or response.status_code != 200:
        return
    if not response.content_type.startswith("text/html"):
        return

    html = response.get_data(as_text=True)
    native_signup_link = 'href="#signup"'
    if native_signup_link not in html:
        return

    response.set_data(html.replace(native_signup_link, 'href="/signup"'))
