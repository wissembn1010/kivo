"""Job boundaries preserve retries and isolate ordinary per-item failures."""
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.billing import bank


class IntegrationTestBankJobFailureIsolation(IntegrationTestCase):
    def test_maintenance_failure_continues_other_payments_and_subscriptions(self):
        with patch.object(bank, "require_admin"), patch.object(bank.frappe, "get_all", side_effect=[
                ["bad-payment", "good-payment"], ["subscription"]]), \
                patch.object(bank, "maintain_payment", side_effect=[ValueError("private-bank-record"), None]) as payments, \
                patch.object(bank, "maintain_subscription") as subscriptions, \
                patch.object(bank.frappe, "log_error") as log:
            bank.daily_maintenance()
        self.assertEqual(payments.call_count, 2)
        subscriptions.assert_called_once_with("subscription")
        self.assertNotIn("private-bank-record", repr(log.call_args))
        self.assertTrue(log.call_args.kwargs["defer_insert"])

    def test_maintenance_deadlock_fails_job_instead_of_reporting_partial_success(self):
        with patch.object(bank, "require_admin"), patch.object(bank.frappe, "get_all", return_value=["payment"]), \
                patch.object(bank, "maintain_payment", side_effect=frappe.QueryDeadlockError()), \
                patch.object(bank, "maintain_subscription") as subscriptions:
            with self.assertRaises(frappe.QueryDeadlockError):
                bank.daily_maintenance()
        subscriptions.assert_not_called()

    def test_reconciliation_ordinary_failure_continues_without_logging_bank_content(self):
        with patch.object(bank, "require_admin"), patch.object(bank, "reconcile_transaction", side_effect=[
                ValueError("bank-secret"), {"status": "MATCHED"}]) as reconcile, \
                patch.object(bank.frappe, "log_error") as log:
            result = bank.reconcile_transactions([{}, {}], "authorized-source")
        self.assertEqual(result, [{"status": "ERROR"}, {"status": "MATCHED"}])
        self.assertEqual(reconcile.call_count, 2)
        self.assertNotIn("bank-secret", repr(log.call_args))

    def test_reconciliation_deadlock_stops_batch_and_propagates_to_scheduler(self):
        with patch.object(bank, "require_admin"), patch.object(bank, "reconcile_transaction", side_effect=[
                {"status": "MATCHED"}, frappe.QueryDeadlockError(), {"status": "MATCHED"}]) as reconcile:
            with self.assertRaises(frappe.QueryDeadlockError):
                bank.reconcile_transactions([{}, {}, {}], "authorized-source")
        self.assertEqual(reconcile.call_count, 2)
        adapter = "creditflow.tests.authorized_export"
        with patch.object(bank, "require_admin"), patch.dict(frappe.conf, {"creditflow_bank_reconciliation_adapter": adapter}), \
                patch.object(bank.frappe, "get_installed_apps", return_value=["creditflow"]), \
                patch.object(bank.frappe, "get_attr", return_value=lambda: []), \
                patch.object(bank, "reconcile_transactions", side_effect=frappe.QueryDeadlockError()):
            with self.assertRaises(frappe.QueryDeadlockError):
                bank.scheduled_reconciliation()
