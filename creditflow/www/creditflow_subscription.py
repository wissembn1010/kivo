import frappe

from creditflow.billing.service import billing_history, is_paid_checkout_enabled
from creditflow.saas_access import get_owner_subscription_summary


def get_context(context):
    context.no_cache = 1
    context.title = "Kivo Subscription"
    context.summary = get_owner_subscription_summary()
    context.checkout_enabled = is_paid_checkout_enabled()
    context.plans = frappe.get_all("CreditFlow Plan", filters={"enabled":1}, fields=["name","plan_name","plan_code","monthly_price","annual_price","description"], order_by="monthly_price asc") if context.checkout_enabled else []
    context.payments = billing_history(context.summary["business"])
