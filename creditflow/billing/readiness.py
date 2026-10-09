from urllib.parse import urlparse

import frappe
from frappe.utils import cint

from creditflow.billing.providers.konnect import PRODUCTION_BASE, SANDBOX_BASE

WEBHOOK_PATH = "/api/method/creditflow.billing.api.konnect_webhook"


def get_bank_readiness():
    """Configuration diagnostics; adapter presence does not prove bank connectivity."""
    from creditflow.billing.bank import bank_settings
    from creditflow.billing.service import is_paid_checkout_enabled
    settings = bank_settings()
    return {
        "checkout_enabled": is_paid_checkout_enabled(),
        "mode": "CONFIGURED_ADAPTER" if frappe.conf.get("creditflow_bank_reconciliation_adapter") else "MANUAL",
        "source_connectivity_verified": False,
        "rib": settings["rib"],
        "bank_name_configured": bool(settings["bank_name"]),
        "beneficiary_name_configured": bool(settings["beneficiary_name"]),
        "payment_expiry_days": settings["expiry_days"],
    }


def get_konnect_readiness():
    """Historical provider diagnostics only; Konnect is retired from checkout."""
    api_key_configured = bool(frappe.conf.get("creditflow_konnect_api_key"))
    wallet_configured = bool(frappe.conf.get("creditflow_konnect_receiver_wallet_id"))
    sandbox = bool(cint(frappe.conf.get("creditflow_konnect_sandbox", 1)))
    public_base_url = (frappe.conf.get("creditflow_public_base_url") or "").rstrip("/")
    parsed = urlparse(public_base_url)
    public_url_valid = parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username
    issues = ["Konnect billing is retired; use bank transfer billing"]
    if not api_key_configured:
        issues.append("Konnect API key is not configured")
    if not wallet_configured:
        issues.append("Konnect receiver wallet is not configured")
    if not public_base_url:
        issues.append("CreditFlow public base URL is not configured")
    elif not public_url_valid:
        issues.append("CreditFlow public base URL must be an absolute HTTPS origin")
    return {
        "ready": False,
        "retired": True,
        "api_key_configured": api_key_configured,
        "receiver_wallet_configured": wallet_configured,
        "mode": "SANDBOX" if sandbox else "PRODUCTION",
        "api_base_url": SANDBOX_BASE if sandbox else PRODUCTION_BASE,
        "public_base_url": public_base_url or None,
        "webhook_url": None,
        "issues": issues,
    }
