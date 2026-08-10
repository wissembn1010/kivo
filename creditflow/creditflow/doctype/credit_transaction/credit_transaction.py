import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


INCREASE_TYPES = {
    "Opening Balance",
    "Sale",
    "Adjustment Increase",
}

DECREASE_TYPES = {
    "Payment",
    "Return",
    "Adjustment Decrease",
}


class CreditTransaction(Document):
    def validate(self):
        if flt(self.amount) <= 0:
            frappe.throw(_("Amount must be greater than zero."))

    def on_submit(self):
        update_customer_balance(self.customer)

    def on_cancel(self):
        update_customer_balance(self.customer)


def update_customer_balance(customer):
    transactions = frappe.get_all(
        "Credit Transaction",
        filters={
            "customer": customer,
            "docstatus": 1,
        },
        fields=[
            "transaction_type",
            "amount",
        ],
    )

    balance = 0.0

    for transaction in transactions:
        amount = flt(transaction.amount)

        if transaction.transaction_type in INCREASE_TYPES:
            balance += amount

        elif transaction.transaction_type in DECREASE_TYPES:
            balance -= amount

    frappe.db.set_value(
        "Customer",
        customer,
        "current_balance",
        balance,
        update_modified=False,
    )