import frappe

from creditflow.signup import inspect_verification_token


def get_context(context):
    context.no_cache = 1
    context.title = "Verify your CreditFlow email"
    context.token = frappe.form_dict.get("token") or ""
    context.verification = inspect_verification_token(context.token)
