import frappe
from frappe import _
from frappe.model.document import Document


class CreditFlowSubscription(Document):
    def on_update(self):
        from creditflow.subscription import clear_request_cache
        clear_request_cache()

    def on_trash(self):
        from creditflow.subscription import clear_request_cache
        clear_request_cache()

    def validate(self):
        if self.status == "TRIAL":
            if not self.trial_start or not self.trial_end:
                frappe.throw(_("Trial Start and Trial End are required for a trial subscription."))
            if self.trial_end <= self.trial_start:
                frappe.throw(_("Trial End must be after Trial Start."))
        if self.current_period_start and self.current_period_end and self.current_period_end < self.current_period_start:
            frappe.throw(_("Current Period End cannot be before Current Period Start."))
        duplicate = frappe.db.get_value("CreditFlow Subscription", {"business": self.business, "name": ["!=", self.name]}, "name")
        if duplicate:
            frappe.throw(_("Business {0} already has a subscription.").format(self.business))
