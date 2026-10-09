import frappe
from frappe import _
from frappe.utils import cint

from creditflow.billing.providers.base import BillingProvider

SANDBOX_BASE = "https://api.sandbox.konnect.network/api/v2"
PRODUCTION_BASE = "https://api.konnect.network/api/v2"


class KonnectProvider(BillingProvider):
    """Retired compatibility shell; it cannot initiate or reconcile payments."""

    def __init__(self):
        self.api_key = frappe.conf.get("creditflow_konnect_api_key")
        self.wallet_id = frappe.conf.get("creditflow_konnect_receiver_wallet_id")
        self.public_base_url = (frappe.conf.get("creditflow_public_base_url") or "").rstrip("/")
        self.sandbox = bool(cint(frappe.conf.get("creditflow_konnect_sandbox", 1)))
        if not self.api_key or not self.wallet_id or not self.public_base_url:
            frappe.throw(_("Konnect billing is not configured."))
        if not self.public_base_url.startswith("https://") and not frappe.conf.developer_mode:
            frappe.throw(_("CreditFlow billing requires an HTTPS public base URL."))
        self.base_url = SANDBOX_BASE if self.sandbox else PRODUCTION_BASE

    def _request(self, method, path, **kwargs):
        # Retired transport is deliberately fail-closed even if future internal
        # code accidentally instantiates this compatibility adapter.
        frappe.throw(_("Konnect billing is retired. Use bank transfer billing."), frappe.PermissionError)

    @property
    def headers(self):
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    def create_checkout(self, payment, customer):
        frappe.throw(_("Konnect billing is retired. Use bank transfer billing."), frappe.PermissionError)

    def get_payment_status(self, provider_payment_id):
        frappe.throw(_("Konnect billing is retired. Use bank transfer billing."), frappe.PermissionError)
