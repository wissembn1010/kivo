import json
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow import onboarding
from creditflow.tests.http_test_support import request


class IntegrationTestOnboardingRequests(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.token = uuid4().hex
        self.businesses = []
        self.users = []
        self.addCleanup(self.cleanup_records)
        for label in ("Own", "Other"):
            self.businesses.append(frappe.get_doc({"doctype": "Business",
                "business_name": f"HTTP Import {label} {self.token}"}).insert().name)
        self.business, self.other = self.businesses
        self.owner, self.owner_headers = self.make_user("OWNER")
        self.staff, self.staff_headers = self.make_user("STAFF")
        # The WSGI request uses a separate connection, as a real client does.
        frappe.db.commit()

    def make_user(self, role):
        secret, key = uuid4().hex, uuid4().hex
        user = frappe.get_doc({"doctype": "User", "email": f"http-{role.lower()}-{self.token}@example.com",
            "first_name": "HTTP Import", "send_welcome_email": 0,
            "creditflow_business": self.business, "api_key": key, "api_secret": secret}).insert()
        user.add_roles(role)
        self.users.append(user.name)
        return user.name, {"Authorization": f"token {key}:{secret}"}

    def cleanup_records(self):
        frappe.db.rollback()
        frappe.set_user("Administrator")
        for name in frappe.get_all("Onboarding Import Batch", filters={"business": ["in", self.businesses]}, pluck="name"):
            frappe.db.delete("Onboarding Import Result", {"parent": name})
            frappe.db.delete("Onboarding Import Batch", {"name": name})
        for dt in ("Credit Transaction", "Customer", "Supplier"):
            frappe.db.delete(dt, {"business": ["in", self.businesses]})
        for user in self.users:
            frappe.delete_doc("User", user, ignore_permissions=True, force=True)
        for business in self.businesses:
            frappe.delete_doc("Business", business, ignore_permissions=True, force=True)
        frappe.db.commit()

    def post(self, content, *, headers=None, **extra):
        result = request("/api/method/creditflow.onboarding.commit_csv", payload={
            "import_type": "CUSTOMERS", "csv_content": content, "filename": "http.csv", **extra},
            headers=self.owner_headers if headers is None else headers)
        frappe.db.rollback()
        return result

    def batch(self):
        names = frappe.get_all("Onboarding Import Batch", filters={"business": self.business}, pluck="name")
        self.assertEqual(len(names), 1)
        return frappe.get_doc("Onboarding Import Batch", names[0])

    def test_http_validation_failure_retains_failed_audit_without_customer(self):
        status, body = self.post("customer_name,credit_limit\nValid,10\nInvalid,-1\n")
        self.assertEqual(status, 417)
        self.assertEqual(body["exc_type"], "ValidationError")
        self.assertEqual(frappe.db.count("Customer", {"business": self.business}), 0)
        batch = self.batch()
        self.assertEqual((batch.status, batch.success_count, batch.failure_count), ("FAILED", 0, 1))
        self.assertEqual(batch.imported_by, self.owner)
        self.assertTrue(all(not row.created_document for row in batch.created_records))

    def test_http_mid_batch_failure_rolls_back_processed_rows_and_retains_audit(self):
        real = onboarding._commit_row
        processed = []

        def fail_second(*args, **kwargs):
            if processed:
                raise RuntimeError("forced HTTP import failure")
            name = real(*args, **kwargs)
            processed.append(name)
            return name

        with patch("creditflow.onboarding._commit_row", side_effect=fail_second):
            status, _ = self.post("customer_name\nFirst\nSecond\n")
        self.assertEqual(status, 500)
        self.assertEqual(len(processed), 1)
        self.assertFalse(frappe.db.exists("Customer", processed[0]))
        self.assertEqual(frappe.db.count("Customer", {"business": self.business}), 0)
        batch = self.batch()
        self.assertEqual((batch.status, batch.success_count, batch.failure_count), ("FAILED", 0, 2))
        self.assertTrue(all(row.result_status == "ROLLED BACK" and not row.created_document for row in batch.created_records))

    def test_http_success_commits_all_rows_and_audit(self):
        status, body = self.post("customer_name\nFirst\nSecond\n")
        self.assertEqual(status, 200)
        self.assertEqual(body["message"]["created_count"], 2)
        self.assertEqual(frappe.db.count("Customer", {"business": self.business}), 2)
        self.assertEqual(self.batch().status, "SUCCESS")

    def test_http_unauthorized_imports_create_neither_data_nor_audit(self):
        for headers, extra in (({}, {}), (self.staff_headers, {}), (self.owner_headers, {"business": self.other})):
            status, _ = self.post("customer_name\nDenied\n", headers=headers, **extra)
            self.assertIn(status, (401, 403))
        for dt in ("Customer", "Onboarding Import Batch"):
            self.assertEqual(frappe.db.count(dt, {"business": ["in", self.businesses]}), 0)

    def test_http_failure_audit_does_not_commit_other_request_writes(self):
        from creditflow.subscription import require_feature

        def write_before_import(*args, **kwargs):
            result = require_feature(*args, **kwargs)
            frappe.db.set_value("Business", self.business, "phone", "must-roll-back")
            return result

        with patch("creditflow.subscription.require_feature", side_effect=write_before_import):
            status, _ = self.post("customer_name,credit_limit\nInvalid,-1\n")
        self.assertEqual(status, 417)
        self.assertNotEqual(frappe.db.get_value("Business", self.business, "phone"), "must-roll-back")
        self.assertEqual(self.batch().status, "FAILED")

    def test_http_header_validation_failure_is_audited(self):
        for _ in range(2):
            status, _ = self.post("customer_name,unknown\nInvalid,value\n")
            self.assertEqual(status, 417)
            self.assertEqual(self.batch().status, "FAILED")
        self.assertEqual(frappe.db.count("Customer", {"business": self.business}), 0)

    def test_http_empty_import_is_audited(self):
        status, _ = self.post("customer_name\n")
        self.assertEqual(status, 417)
        self.assertEqual(self.batch().status, "FAILED")

    def test_http_get_cannot_import(self):
        status, _ = request("/api/method/creditflow.onboarding.commit_csv", method="GET",
            payload={"import_type": "CUSTOMERS", "csv_content": "customer_name\nDenied\n"},
            headers=self.owner_headers)
        self.assertIn(status, (403, 405))
        frappe.db.rollback()
        self.assertEqual(frappe.db.count("Onboarding Import Batch", {"business": self.business}), 0)

    def test_http_retry_can_succeed_without_a_stale_failure_overwriting_it(self):
        content = "customer_name\nRetry\n"
        with patch("creditflow.onboarding._commit_row", side_effect=RuntimeError("retryable failure")):
            status, _ = self.post(content)
        self.assertEqual(status, 500)
        failed = self.batch()
        frappe.set_user(self.owner)
        preview = onboarding.preview_csv("CUSTOMERS", content)
        frappe.set_user("Administrator")
        status, body = self.post(content)
        self.assertEqual(status, 200)
        self.assertEqual(body["message"]["batch"], failed.name)
        onboarding._persist_failed_import(preview, "late failure")
        frappe.db.rollback()
        self.assertEqual(self.batch().status, "SUCCESS")
        self.assertEqual(frappe.db.count("Customer", {"business": self.business}), 1)

    def test_http_invalid_long_data_cannot_prevent_failure_audit(self):
        status, _ = self.post("customer_name\n" + "X" * 1000 + "\n", filename="F" * 300)
        self.assertEqual(status, 417)
        batch = self.batch()
        self.assertEqual(batch.status, "FAILED")
        self.assertEqual(frappe.db.count("Customer", {"business": self.business}), 0)

    def test_http_mid_batch_ledger_failure_rolls_back_submitted_entries(self):
        customer = frappe.get_doc({"doctype": "Customer", "business": self.business,
            "customer_name": "Opening HTTP"}).insert().name
        frappe.db.commit()
        original = onboarding._commit_row
        processed = []
        def fail_second(*args, **kwargs):
            if processed: raise RuntimeError("forced ledger row failure")
            name = original(*args, **kwargs)
            self.assertEqual(frappe.db.get_value("Credit Transaction", name, "docstatus"), 1)
            processed.append(name)
            return name
        with patch("creditflow.onboarding._commit_row", side_effect=fail_second):
            status, _ = self.post(f"customer,amount,reference\n{customer},10.125,first\n{customer},20.250,second\n",
                import_type="OPENING_CUSTOMER_DEBT")
        self.assertEqual(status, 500)
        self.assertEqual(len(processed), 1)
        self.assertEqual(frappe.db.count("Credit Transaction", {"business": self.business}), 0)
        self.assertEqual(self.batch().status, "FAILED")

    def test_http_out_of_range_opening_amount_still_audits_failure(self):
        customer = frappe.get_doc({"doctype": "Customer", "business": self.business,
            "customer_name": "Overflow HTTP"}).insert().name
        frappe.db.commit()
        status, _ = self.post(f"customer,amount\n{customer},1e100\n", import_type="OPENING_CUSTOMER_DEBT")
        self.assertGreaterEqual(status, 400)
        self.assertEqual(frappe.db.count("Credit Transaction", {"business": self.business}), 0)
        self.assertEqual(self.batch().status, "FAILED")
        self.assertEqual(self.batch().total_amount, 0)
        self.assertEqual(Decimal(json.loads(self.batch().errors)[0]["attempted_total"]), Decimal("1e100"))

    def test_http_csv_parser_failure_is_audited(self):
        import csv
        status, _ = self.post("customer_name\n" + "X" * (csv.field_size_limit() + 1) + "\n")
        self.assertGreaterEqual(status, 400)
        self.assertEqual(frappe.db.count("Customer", {"business": self.business}), 0)
        self.assertEqual(self.batch().status, "FAILED")

    def test_audit_cleanup_failures_restore_request_connection_and_release_audit_connection(self):
        original_connect = frappe.connect
        for failure in ("rollback", "close"):
            with self.subTest(failure=failure):
                frappe.set_user(self.owner)
                preview = onboarding.preview_csv("CUSTOMERS", f"customer_name,credit_limit\nCleanup {failure},-1\n")
                request_db = frappe.local.db
                audit_connections = []
                patches = []

                def connect(*args, **kwargs):
                    original_connect(*args, **kwargs)
                    audit = frappe.local.db
                    audit_connections.append(audit)
                    real_close = audit.close
                    def close_then_fail():
                        real_close()
                        raise RuntimeError("injected close failure")
                    injected = patch.object(audit, failure,
                        side_effect=RuntimeError("injected rollback failure") if failure == "rollback" else close_then_fail)
                    injected.start(); patches.append(injected)

                try:
                    with patch("frappe.connect", side_effect=connect):
                        onboarding._persist_failed_import(preview, "test failure")
                    self.assertIs(frappe.local.db, request_db)
                    self.assertEqual(len(audit_connections), 1)
                    self.assertIsNone(audit_connections[0]._conn)
                    request_db.rollback()
                    self.assertEqual(frappe.db.get_value("Onboarding Import Batch",
                        {"business": self.business, "import_key": preview["import_key"]}, "status"), "FAILED")
                finally:
                    for injected in patches: injected.stop()
                    for audit in audit_connections: audit.close()
                    frappe.local.db = request_db
                    frappe.set_user("Administrator")

    def test_audit_insert_failure_rolls_back_partial_audit_and_releases_lock(self):
        frappe.set_user(self.owner)
        preview = onboarding.preview_csv("CUSTOMERS", "customer_name,credit_limit\nAudit failure,-1\n")
        request_db = frappe.local.db
        original_save = onboarding._save_batch
        def fail_after_insert(*args, **kwargs):
            original_save(*args, **kwargs)
            raise RuntimeError("forced audit persistence failure")
        with patch("creditflow.onboarding._save_batch", side_effect=fail_after_insert):
            onboarding._persist_failed_import(preview, "row failed")
        self.assertIs(frappe.local.db, request_db)
        frappe.db.rollback()
        self.assertFalse(frappe.db.exists("Onboarding Import Batch", {"import_key": preview["import_key"]}))
        # Another connection can acquire the Business lock and persist the audit.
        onboarding._persist_failed_import(preview, "row failed")
        frappe.db.rollback()
        self.assertEqual(self.batch().status, "FAILED")

    def test_failed_audit_does_not_persist_private_values_from_processing_exception(self):
        private_note = "PRIVATE_IMPORT_NOTE_" + self.token
        with patch("creditflow.onboarding._commit_row", side_effect=RuntimeError(private_note)):
            status, _ = self.post(f"customer_name,notes\nPublic identifier,{private_note}\n")
        self.assertEqual(status, 500)
        batch = self.batch()
        self.assertEqual(batch.status, "FAILED")
        self.assertNotIn(private_note, batch.as_json())
        self.assertIn("RuntimeError", batch.errors)
        self.assertEqual(frappe.db.count("Customer", {"business": self.business}), 0)

    def test_duplicate_opening_audit_does_not_persist_private_note_fingerprint(self):
        customer = frappe.get_doc({"doctype": "Customer", "business": self.business,
            "customer_name": "Private opening note"}).insert().name
        frappe.db.commit()
        private_note = "PRIVATE_OPENING_NOTE_" + self.token
        row = f"{customer},10.125,{private_note}\n"
        status, _ = self.post("customer,amount,note\n" + row + row, import_type="OPENING_CUSTOMER_DEBT")
        self.assertEqual(status, 417)
        batch = self.batch()
        self.assertEqual(batch.status, "FAILED")
        self.assertNotIn(private_note.lower(), batch.as_json().lower())
        self.assertIn("first appeared on row 2", batch.errors)
        self.assertEqual(frappe.db.count("Credit Transaction", {"business": self.business}), 0)
