from frappe.model.document import Document


class CreditFlowBankTransaction(Document):
    def validate(self):
        from creditflow.billing.bank import guard_record
        guard_record(self)

    def before_change(self):
        from creditflow.billing.bank import guard_record
        guard_record(self)

    def on_trash(self):
        from creditflow.billing.bank import guard_record
        guard_record(self)
