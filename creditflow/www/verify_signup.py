import frappe

from creditflow.signup import inspect_verification_token


INVALID_VERIFICATION = {"valid": False, "state": "INVALID"}
MAX_TOKEN_LENGTH = 256


def get_context(context):
    context.no_cache = 1
    context.title = "Verify your Kivo email"
    context.token = ""
    context.verification = INVALID_VERIFICATION.copy()

    token = frappe.form_dict.get("token")
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        return

    context.token = token
    context.verification = inspect_verification_token(token)
