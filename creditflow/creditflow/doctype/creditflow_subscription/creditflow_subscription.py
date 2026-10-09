import frappe
from frappe import _
from frappe.model.document import Document


class CreditFlowSubscription(Document):
    def on_update(self):
        from creditflow.subscription import clear_request_cache
        clear_request_cache()

    def on_change(self):
        # db_set runs on_change rather than on_update; do not retain old access
        # after administrative revocation in the same request or job.
        from creditflow.subscription import clear_request_cache
        clear_request_cache()

    def on_trash(self):
        self._require_administrative_mutation()
        from creditflow.subscription import clear_request_cache
        clear_request_cache()

    def validate(self):
        # Ignore-permissions saves are used for trusted initial trials. They must
        # never let a tenant change an existing paid entitlement.
        if not self.is_new() or self.status != "TRIAL":
            self._require_administrative_mutation()
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

    def before_change(self):
        self._require_administrative_mutation()

    def _require_administrative_mutation(self):
        from creditflow.permissions import has_cross_business_access
        from creditflow.billing.bank import _mutation

        if not has_cross_business_access() and not _mutation.get():
            frappe.throw(_("Only authorized administrators can change subscription entitlements."), frappe.PermissionError)
