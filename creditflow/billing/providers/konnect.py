import frappe
from frappe import _
from frappe.utils import cint
from frappe.integrations.utils import make_get_request, make_post_request

from creditflow.billing.providers.base import BillingProvider

SANDBOX_BASE = "https://api.sandbox.konnect.network/api/v2"
PRODUCTION_BASE = "https://api.konnect.network/api/v2"


class KonnectProvider(BillingProvider):
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

    @property
    def headers(self):
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    def create_checkout(self, payment, customer):
        payload = {
            "receiverWalletId": self.wallet_id,
            "token": payment.currency,
            "amount": payment.amount_minor,
            "type": "immediate",
            "description": f"CreditFlow {payment.plan} {payment.billing_cycle.lower()} subscription",
            "acceptedPaymentMethods": ["wallet", "bank_card", "e-DINAR"],
            "lifespan": 30,
            "checkoutForm": True,
            "addPaymentFeesToAmount": False,
            "firstName": customer.get("first_name"),
            "lastName": customer.get("last_name"),
            "phoneNumber": customer.get("phone"),
            "email": customer.get("email"),
            "orderId": payment.internal_order_id,
            "webhook": f"{self.public_base_url}/api/method/creditflow.billing.api.konnect_webhook",
            "theme": "light",
        }
        response = make_post_request(f"{self.base_url}/payments/init-payment", headers=self.headers, json=payload, timeout=15)
        if not isinstance(response, dict) or not response.get("paymentRef") or not response.get("payUrl"):
            raise frappe.ValidationError("Konnect returned a malformed checkout response.")
        return {"provider_payment_id": response["paymentRef"], "checkout_url": response["payUrl"]}

    def get_payment_status(self, provider_payment_id):
        response = make_get_request(f"{self.base_url}/payments/{provider_payment_id}", headers=self.headers, timeout=15)
        payment = response.get("payment") if isinstance(response, dict) else None
        if not isinstance(payment, dict):
            raise frappe.ValidationError("Konnect returned malformed payment details.")
        status = str(payment.get("status") or "").lower()
        normalized = {"completed":"SUCCEEDED", "pending":"PENDING"}.get(status, "PENDING")
        transactions = payment.get("transactions") or []
        transaction_success = any(str(item.get("status") or "").lower() == "success" for item in transactions if isinstance(item, dict))
        return {
            "normalized_status": normalized,
            "provider_status": status,
            "amount_minor": payment.get("reachedAmount") if payment.get("reachedAmount") is not None else payment.get("amount"),
            "currency": payment.get("token"),
            "order_id": payment.get("orderId"),
            "receiver_wallet_id": (payment.get("receiverWallet") or {}).get("id") or (payment.get("receiverWallet") or {}).get("_id"),
            "transaction_success": transaction_success if transactions else None,
            "payment_id": payment.get("id"),
        }
