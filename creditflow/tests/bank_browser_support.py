"""Synthetic browser fixtures; deliberately restricted to the dedicated test site."""
import json
import os
import secrets
from pathlib import Path

import frappe
from frappe.utils import add_days, now_datetime

STATE = Path("/tmp/kivo-bank-production-review/fixture.json")


def _test_site():
    if frappe.local.site != "kivo-fix001-tests.localhost" or not frappe.conf.get("allow_tests"):
        raise RuntimeError("Browser fixture tools only run on the dedicated test site")
    frappe.set_user("Administrator")


def setup():
    _test_site()
    if STATE.exists():
        raise RuntimeError("Clean up the existing synthetic fixture before creating another")
    token = secrets.token_hex(6)
    state = {"token": token, "users": {}, "password": secrets.token_urlsafe(24),
        "previous_checkout": frappe.conf.get("creditflow_paid_checkout_enabled"),
        "had_checkout": "creditflow_paid_checkout_enabled" in frappe.conf}
    business = frappe.get_doc({"doctype": "Business", "business_name": "Browser Bank " + token,
        "onboarding_status": "COMPLETED"}).insert()
    state["business"] = business.name
    for label, role in (("owner", "OWNER"), ("admin", "System Manager")):
        user = frappe.get_doc({"doctype": "User", "email": f"bank-browser-{label}-{token}@example.invalid",
            "first_name": "Browser " + label, "send_welcome_email": 0, "language": "en",
            "user_type": "System User", "new_password": state["password"],
            "creditflow_business": business.name if label == "owner" else None}).insert()
        user.add_roles(role)
        state["users"][label] = user.name
    plan = frappe.get_doc({"doctype": "CreditFlow Plan", "plan_name": "Browser Plan " + token,
        "plan_code": "BROWSER_" + token.upper(), "monthly_price": 79, "annual_price": 790,
        "enabled": 1, "entitlements": [{"entitlement_key": "migration", "value_type": "BOOLEAN", "value": "true"}]}).insert()
    state["plan"] = plan.name
    sub = frappe.get_doc({"doctype": "CreditFlow Subscription", "business": business.name,
        "plan": plan.name, "status": "TRIAL", "trial_start": add_days(now_datetime(), -15),
        "trial_end": add_days(now_datetime(), -1)}).insert()
    state["subscription"] = sub.name
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state))
    os.chmod(STATE, 0o600)
    frappe.db.commit()
    from frappe.installer import update_site_config
    update_site_config("creditflow_paid_checkout_enabled", 1)
    return {"fixture": str(STATE), "synthetic_records_only": True}


def expire():
    _test_site()
    state = json.loads(STATE.read_text())
    from creditflow.billing.bank import maintain_subscription
    frappe.db.set_value("CreditFlow Subscription", state["subscription"], {
        "current_period_start": add_days(now_datetime(), -32),
        "current_period_end": add_days(now_datetime(), -1),
    })
    maintain_subscription(state["subscription"])
    frappe.db.commit()
    return {"synthetic_subscription_status": frappe.db.get_value("CreditFlow Subscription", state["subscription"], "status")}


def evidence():
    _test_site()
    state = json.loads(STATE.read_text())
    payments = frappe.get_all("CreditFlow Billing Payment", filters={"business": state["business"]},
        fields=["name", "status", "amount", "internal_order_id", "billing_period_start", "billing_period_end", "bank_transaction"])
    names = [p.name for p in payments]
    return {"site": frappe.local.site, "payments": payments,
        "subscription_status": frappe.db.get_value("CreditFlow Subscription", state["subscription"], "status"),
        "audit": frappe.get_all("CreditFlow Billing Audit", filters={"payment": ["in", names]},
            fields=["event", "actor", "occurred_at"], order_by="creation asc") if names else []}


def run_scoped_jobs():
    """Exercise native job dispatch, scoped to this run's synthetic Business."""
    _test_site()
    state = json.loads(STATE.read_text())
    if frappe.conf.get("creditflow_bank_reconciliation_adapter"):
        raise RuntimeError("Test job probe must not contact a configured bank source")
    from unittest.mock import patch
    original = frappe.get_all

    def scoped_get_all(doctype, *args, **kwargs):
        if doctype in {"CreditFlow Billing Payment", "CreditFlow Subscription"}:
            kwargs["filters"] = dict(kwargs.get("filters") or {}) | {"business": state["business"]}
        return original(doctype, *args, **kwargs)

    results = []
    for method in ("creditflow.billing.bank.scheduled_reconciliation", "creditflow.billing.bank.daily_maintenance"):
        name = frappe.db.get_value("Scheduled Job Type", {"method": method}, "name")
        job = frappe.get_doc("Scheduled Job Type", name)
        if job.stopped:
            raise RuntimeError("The test site's billing job is individually disabled")
        with patch("frappe.get_all", scoped_get_all), patch.object(job, "log_status", wraps=job.log_status) as logged:
            job.execute()
        statuses = [call.args[0] for call in logged.call_args_list]
        if statuses != ["Start", "Complete"]:
            raise RuntimeError("The isolated billing job did not complete successfully")
        results.append({"method": method, "stopped": job.reload().stopped, "last_execution": job.last_execution, "statuses": statuses})
    frappe.db.commit()
    return {"native_dispatch": results, "scope": "synthetic Business only", "global_scheduler_enabled": False}


def cleanup():
    _test_site()
    if not STATE.exists():
        return {"cleaned": False}
    state = json.loads(STATE.read_text())
    from creditflow.billing.bank import billing_mutation
    payments = frappe.get_all("CreditFlow Billing Payment", filters={"business": state["business"]}, pluck="name")
    with billing_mutation():
        for name in frappe.get_all("File", filters={"attached_to_doctype": "CreditFlow Billing Payment",
                "attached_to_name": ["in", payments]}, pluck="name") if payments else []:
            frappe.delete_doc("File", name, ignore_permissions=True)
    for dt in ("CreditFlow Billing Audit", "CreditFlow Billing Payment", "CreditFlow Subscription", "Customer"):
        frappe.db.delete(dt, {"business": state["business"]})
    if payments:
        frappe.db.delete("CreditFlow Bank Transaction", {"payment": ["in", payments]})
        frappe.db.delete("Notification Log", {"document_type": "CreditFlow Billing Payment", "document_name": ["in", payments]})
    frappe.db.delete("Notification Log", {"document_type": "CreditFlow Subscription", "document_name": state["subscription"]})
    for user in state["users"].values():
        frappe.delete_doc("User", user, force=True, ignore_permissions=True)
    frappe.delete_doc("Business", state["business"], force=True, ignore_permissions=True)
    frappe.delete_doc("CreditFlow Plan", state["plan"], force=True, ignore_permissions=True)
    frappe.db.commit()
    from frappe.installer import update_site_config
    update_site_config("creditflow_paid_checkout_enabled", state["previous_checkout"])
    STATE.unlink()
    return {"cleaned": True, "checkout_configuration_restored": True}
