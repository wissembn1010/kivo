import frappe
from frappe import _

from creditflow.i18n import get_language_context
from creditflow.saas_onboarding import get_onboarding_business


def get_context(context):
    context.no_cache = 1
    context.title = _('Set up your Business')
    context.update(get_language_context())
    context.business = get_onboarding_business()
