import frappe

from creditflow.saas_onboarding import get_onboarding_business


def get_context(context):
    context.no_cache = 1
    context.title = "Set up your Business"
    context.business = frappe._dict()
    context.business = get_onboarding_business()
