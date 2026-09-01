from urllib.parse import urlparse

import frappe
from frappe.utils import cint

from creditflow.billing.providers.konnect import PRODUCTION_BASE, SANDBOX_BASE

WEBHOOK_PATH = "/api/method/creditflow.billing.api.konnect_webhook"


def get_konnect_readiness():
    """Return deployment diagnostics without ever returning secret values."""
    api_key_configured = bool(frappe.conf.get("creditflow_konnect_api_key"))
    wallet_configured = bool(frappe.conf.get("creditflow_konnect_receiver_wallet_id"))
    sandbox = bool(cint(frappe.conf.get("creditflow_konnect_sandbox", 1)))
    public_base_url = (frappe.conf.get("creditflow_public_base_url") or "").rstrip("/")
    parsed = urlparse(public_base_url)
    public_url_valid = parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username
    issues = []
    if not api_key_configured:
        issues.append("Konnect API key is not configured")
    if not wallet_configured:
        issues.append("Konnect receiver wallet is not configured")
    if not public_base_url:
        issues.append("CreditFlow public base URL is not configured")
    elif not public_url_valid:
        issues.append("CreditFlow public base URL must be an absolute HTTPS origin")
    return {
        "ready": not issues,
        "api_key_configured": api_key_configured,
        "receiver_wallet_configured": wallet_configured,
        "mode": "SANDBOX" if sandbox else "PRODUCTION",
        "api_base_url": SANDBOX_BASE if sandbox else PRODUCTION_BASE,
        "public_base_url": public_base_url or None,
        "webhook_url": f"{public_base_url}{WEBHOOK_PATH}" if public_url_valid else None,
        "issues": issues,
    }
