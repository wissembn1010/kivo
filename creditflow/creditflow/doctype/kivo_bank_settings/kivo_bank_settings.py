import frappe
from frappe import _
from frappe.model.document import Document


class KivoBankSettings(Document):
    def validate(self):
        import re
        if not re.fullmatch(r"[0-9]{20}", re.sub(r"\s+", "", self.rib or "")):
            frappe.throw(_("RIB must contain 20 digits."))
        for key in ("payment_expiry_days", "review_reminder_days", "expiration_reminder_days"):
            if not 1 <= int(self.get(key) or 0) <= 365:
                frappe.throw(_("Billing policy days must be between 1 and 365."))
