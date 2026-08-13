from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document


EVENT_DIRECTIONS = {
    "SALE": {"DEBIT"},
    "PAYMENT": {"CREDIT"},
    "RETURN": {"CREDIT"},
    "OPENING_BALANCE": {"DEBIT", "CREDIT"},
}


class CreditTransaction(Document):
    def validate(self):
        self.validate_amount()
        self.validate_direction()
        self.validate_business()

    def validate_amount(self):
        try:
            amount = Decimal(str(self.amount))
        except (InvalidOperation, TypeError):
            frappe.throw(_("Amount must be a valid number."))

        if amount <= Decimal("0"):
            frappe.throw(_("Amount must be greater than zero."))

    def validate_direction(self):
        allowed = EVENT_DIRECTIONS.get(self.transaction_type)

        if not allowed:
            frappe.throw(_("Invalid Event Type."))

        if self.direction not in allowed:
            frappe.throw(
                _("Direction {0} is invalid for {1}.").format(
                    self.direction,
                    self.transaction_type,
                )
            )

    def validate_business(self):
        customer_business = frappe.db.get_value(
            "Customer",
            self.customer,
            "business",
        )

        if customer_business != self.business:
            frappe.throw(
                _("Customer and Credit Transaction must belong to the same Business.")
            )

    def before_cancel(self):
        frappe.throw(
            _("Posted ledger entries cannot be cancelled. Create a reversal instead.")
        )