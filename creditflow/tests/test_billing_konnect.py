"""Bank billing regressions (module name retained for existing test runners)."""
import base64
import json
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_months, get_datetime, now_datetime

from creditflow.billing import bank
from creditflow.billing.service import create_checkout, reconcile_owner_payment, tnd_to_millimes
from creditflow.subscription import clear_request_cache, require_feature


def pdf_receipt():
    from pypdf import PdfWriter
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    stream = BytesIO(); writer.write(stream)
    return base64.b64encode(stream.getvalue()).decode()


class EncodedReceipt(str):
    def __repr__(self):
        return f"<encoded receipt: {len(self)} bytes>"


class IntegrationTestBankBilling(IntegrationTestCase):
    def setUp(self):
        config = patch.dict(frappe.conf, {"creditflow_paid_checkout_enabled": 1})
        config.start(); self.addCleanup(config.stop)
        frappe.set_user("Administrator")
        self.token = uuid4().hex[:10]
        self.business = frappe.get_doc({"doctype": "Business", "business_name": "Bank " + self.token}).insert()
        self.other = frappe.get_doc({"doctype": "Business", "business_name": "Other bank " + self.token}).insert()
        self.owner = self.user("owner", self.business.name, "OWNER")
        self.staff = self.user("staff", self.business.name, "STAFF")
        self.plan = frappe.get_doc({"doctype": "CreditFlow Plan", "plan_name": "Bank " + self.token,
            "plan_code": "BANK_" + self.token.upper(), "enabled": 1, "monthly_price": 59,
            "annual_price": 590, "entitlements": [{"entitlement_key": "advanced_reports",
                "value_type": "BOOLEAN", "value": "true"}]}).insert()
        self.subscription = frappe.get_doc({"doctype": "CreditFlow Subscription", "business": self.business.name,
            "plan": self.plan.name, "status": "TRIAL", "trial_start": add_days(now_datetime(), -15),
            "trial_end": add_days(now_datetime(), -1)}).insert()
        clear_request_cache(); frappe.set_user(self.owner)
        self.addCleanup(frappe.set_user, "Administrator")
        self.addCleanup(clear_request_cache)

    def user(self, prefix, business, role):
        # Synthetic fixture volume must not consume the unrelated signup quota.
        with patch("frappe.core.doctype.user.user.throttle_user_creation"):
            user = frappe.get_doc({"doctype": "User", "email": f"bank-{prefix}-{self.token}@example.com",
                "first_name": prefix, "send_welcome_email": 0, "creditflow_business": business}).insert(ignore_permissions=True)
        user.add_roles(role)
        return user.name

    def checkout(self, cycle="MONTHLY"):
        frappe.set_user(self.owner)
        result = create_checkout(self.plan.name, cycle)
        return result, frappe.get_doc("CreditFlow Billing Payment", result["payment"])

    def review(self, payment, **changes):
        frappe.set_user("Administrator")
        data = {"payment": payment.name, "action": "VERIFIED", "transaction_id": "TX-" + uuid4().hex,
            "amount": payment.amount, "transaction_date": now_datetime().date().isoformat(),
            "reference": payment.internal_order_id, "destination_account": payment.destination_account,
            "notes": "Funds checked in bank account"}
        data.update(changes)
        return bank.admin_review(**data)

    def assert_unpaid(self):
        self.assertEqual(self.subscription.reload().status, "TRIAL")
        clear_request_cache(); frappe.set_user(self.owner)
        with self.assertRaises(frappe.PermissionError):
            require_feature(self.business.name, "advanced_reports")

    def test_checkout_server_prices_and_reference(self):
        result, payment = self.checkout()
        self.assertEqual(payment.status, "PENDING_PAYMENT")
        self.assertEqual(payment.provider, "BANK_TRANSFER")
        self.assertEqual(payment.amount, 59)
        self.assertEqual(payment.currency, "TND")
        self.assertTrue(payment.internal_order_id)
        self.assertTrue(payment.expires_at)
        self.assertFalse(payment.provider_payment_id)
        _, second = self.checkout("ANNUAL")
        self.assertEqual(second.amount, 590)
        self.assertNotEqual(payment.internal_order_id, second.internal_order_id)
        self.assert_unpaid()

    def test_checkout_rejects_authority_injection_and_staff(self):
        for attack in ({"business": self.other.name}, {"amount": ".001"}, {"status": "VERIFIED"},
                {"provider": "KONNECT"}, {"currency": "USD"}):
            with self.assertRaises(frappe.ValidationError):
                create_checkout(self.plan.name, **attack)
        frappe.set_user(self.staff)
        with self.assertRaises(frappe.PermissionError):
            create_checkout(self.plan.name)

    def test_checkout_requires_server_opt_in(self):
        for value in (None, 0, "0", False, "false", "true", 2):
            with self.subTest(value=value), patch.dict(frappe.conf, {"creditflow_paid_checkout_enabled": value}):
                with self.assertRaises(frappe.PermissionError):
                    create_checkout(self.plan.name)

    def test_tnd_conversion_is_exact(self):
        self.assertEqual(tnd_to_millimes("59.500"), 59500)
        for value in ("0", "-1", "1.0001", "NaN"):
            with self.assertRaises(frappe.ValidationError):
                tnd_to_millimes(value)

    def test_receipt_is_private_and_never_activates(self):
        _, payment = self.checkout()
        content = pdf_receipt()
        bank.submit_proof(payment.name, "receipt.pdf", content)
        payment.reload()
        self.assertEqual(payment.status, "PROOF_SUBMITTED")
        files = frappe.get_all("File", filters={"attached_to_doctype": payment.doctype,
            "attached_to_name": payment.name}, fields=["name", "is_private", "file_url"])
        self.assertTrue(files)
        self.assertTrue(all(row.is_private and row.file_url.startswith("/private/files/") for row in files))
        self.assert_unpaid()

    def test_receipt_download_follows_current_business_instead_of_original_file_owner(self):
        _, payment = self.checkout()
        bank.submit_proof(payment.name, "receipt.pdf", pdf_receipt())
        file_name = frappe.db.get_value("File", {"attached_to_doctype": payment.doctype,
            "attached_to_name": payment.name}, "name")
        receipt = frappe.get_doc("File", file_name)
        self.assertEqual(receipt.owner, self.owner)
        self.assertTrue(receipt.is_downloadable())
        frappe.set_user("Administrator")
        frappe.db.set_value("User", self.owner, "creditflow_business", self.other.name)
        frappe.set_user(self.owner)
        self.assertFalse(receipt.is_downloadable())
        frappe.set_user("Administrator")
        self.assertTrue(receipt.is_downloadable())

    def test_malformed_spoofed_and_oversized_receipts_rejected(self):
        _, payment = self.checkout()
        samples = [("proof.pdf", "not-base64!"), ("proof.pdf", base64.b64encode(b"<html>fraud</html>").decode()),
            ("proof.svg", base64.b64encode(b"<svg/>").decode()),
            ("proof.pdf", EncodedReceipt(base64.b64encode(b"%PDF-" + b"x" * (5 * 1024 * 1024)).decode()))]
        for filename, content in samples:
            with self.subTest(filename=filename, size=len(content)), self.assertRaises(frappe.ValidationError):
                bank.submit_proof(payment.name, filename, content)
        self.assertEqual(payment.reload().status, "PENDING_PAYMENT")
        self.assert_unpaid()

    def test_receipt_resubmission_preserves_review_and_terminal_decisions(self):
        _, payment = self.checkout()
        frappe.set_user("Administrator")
        bank.admin_review(payment.name, "UNDER_REVIEW", notes="Investigating received amount")
        reviewed_at = payment.reload().reviewed_at
        frappe.set_user(self.owner)
        bank.submit_proof(payment.name, "supplementary.pdf", pdf_receipt())
        payment.reload()
        self.assertEqual(payment.status, "UNDER_REVIEW")
        self.assertEqual(payment.admin_notes, "Investigating received amount")
        self.assertEqual(payment.reviewed_at, reviewed_at)
        self.assertEqual(frappe.db.count(bank.AUDIT, {"payment": payment.name, "event": "PROOF_SUBMITTED"}), 1)
        self.assert_unpaid()

        frappe.set_user("Administrator")
        bank.admin_review(payment.name, "REJECTED", notes="No received funds")
        _, verified = self.checkout()
        self.review(verified)
        for terminal in (payment, verified):
            frappe.set_user(self.owner)
            status = terminal.reload().status
            receipt = terminal.receipt
            with self.assertRaises(frappe.ValidationError):
                bank.submit_proof(terminal.name, "replacement.pdf", pdf_receipt())
            self.assertEqual(terminal.reload().status, status)
            self.assertEqual(terminal.receipt, receipt)

    def test_owner_staff_and_business_system_manager_cannot_approve(self):
        _, payment = self.checkout()
        frappe.set_user("Administrator")
        bound_admin = self.user("bound-admin", self.business.name, "System Manager")
        for user in (self.owner, self.staff, bound_admin):
            frappe.set_user(user)
            with patch("creditflow.billing.bank.frappe.log_error") as log:
                with self.assertRaises(frappe.PermissionError):
                    bank.admin_review(payment.name, "VERIFIED", transaction_id="BANK_CREDENTIAL_SECRET", amount=59,
                        reference=payment.internal_order_id, transaction_date=now_datetime().date().isoformat(),
                        notes="RAW_PRIVATE_BANK_RECORD")
                log.assert_called_once()
                self.assertTrue(log.call_args.kwargs["defer_insert"])
                message = log.call_args.kwargs["message"]
                self.assertEqual(json.loads(message), {"actor": user, "payment": payment.name,
                    "error_type": "PermissionError"})
                self.assertNotIn("BANK_CREDENTIAL_SECRET", message)
                self.assertNotIn("RAW_PRIVATE_BANK_RECORD", message)
        self.assert_unpaid()

    def test_verification_and_replay_activate_once(self):
        _, payment = self.checkout()
        tx = "TX-" + uuid4().hex
        self.review(payment, transaction_id=tx)
        self.assertEqual(payment.reload().status, "VERIFIED")
        sub = self.subscription.reload()
        self.assertEqual(sub.status, "ACTIVE")
        self.assertEqual(sub.external_provider, "BANK_TRANSFER")
        end = sub.current_period_end
        self.review(payment, transaction_id=tx)
        self.assertEqual(self.subscription.reload().current_period_end, end)
        self.assertTrue(frappe.db.exists("CreditFlow Billing Audit", {"payment": payment.name,
            "event": "VERIFICATION_REPLAY"}))
        clear_request_cache(); frappe.set_user(self.owner)
        self.assertTrue(require_feature(self.business.name, "advanced_reports"))

    def test_wrong_amount_missing_reference_date_and_destination_fail(self):
        _, payment = self.checkout()
        for change in ({"amount": 58}, {"amount": 60}, {"reference": ""}, {"reference": "forged"},
                {"transaction_date": add_days(now_datetime(), -5).date().isoformat()},
                {"transaction_date": add_days(now_datetime(), 365).date().isoformat()},
                {"destination_account": "00000000000000000000"}, {"transaction_id": ""}):
            with self.subTest(change=change), self.assertRaises(frappe.ValidationError):
                self.review(payment, **change)
            self.assertEqual(payment.reload().status, "PENDING_PAYMENT")
        self.assert_unpaid()

    def test_duplicate_transaction_cannot_credit_another_payment(self):
        _, first = self.checkout(); _, second = self.checkout()
        tx = "TX-" + uuid4().hex
        self.review(first, transaction_id=tx)
        end = self.subscription.reload().current_period_end
        with self.assertRaises((frappe.ValidationError, frappe.DuplicateEntryError, frappe.UniqueValidationError)):
            self.review(second, transaction_id=tx)
        self.assertEqual(second.reload().status, "PENDING_PAYMENT")
        self.assertEqual(self.subscription.reload().current_period_end, end)

    def test_early_annual_renewal_preserves_paid_time(self):
        _, payment = self.checkout(); self.review(payment)
        end = get_datetime(self.subscription.reload().current_period_end)
        _, renewal = self.checkout("ANNUAL"); self.review(renewal)
        self.assertEqual(self.subscription.reload().current_period_end, add_months(end, 12))
        clear_request_cache(); frappe.set_user(self.owner)
        self.assertTrue(require_feature(self.business.name, "advanced_reports"))

    def test_rejection_and_owner_refresh_never_activate(self):
        _, payment = self.checkout()
        self.review(payment, action="UNDER_REVIEW")
        self.assertEqual(payment.reload().status, "UNDER_REVIEW")
        self.review(payment, action="REJECTED", notes="Funds not received")
        self.assertEqual(payment.reload().status, "REJECTED")
        frappe.set_user(self.owner)
        self.assertEqual(reconcile_owner_payment(payment.name)["status"], "REJECTED")
        self.assert_unpaid()

    def test_cross_tenant_status_receipt_and_direct_writes_denied(self):
        _, payment = self.checkout()
        frappe.set_user("Administrator"); other = self.user("other-owner", self.other.name, "OWNER")
        frappe.set_user(other)
        with self.assertRaises(frappe.PermissionError):
            reconcile_owner_payment(payment.name)
        with self.assertRaises(frappe.PermissionError):
            bank.submit_proof(payment.name, "receipt.pdf", base64.b64encode(b"%PDF-1.4\n%%EOF").decode())
        self.assertFalse(payment.has_permission("read"))
        frappe.set_user(self.owner); payment.status = "VERIFIED"
        with self.assertRaises(frappe.PermissionError):
            payment.save()
        sub = self.subscription.reload(); sub.status = "ACTIVE"
        with self.assertRaises(frappe.PermissionError):
            sub.save()

    def test_expiration_and_cancellation_checked_without_waiting_for_job(self):
        _, payment = self.checkout(); self.review(payment)
        for cancelled in (0, 1):
            frappe.set_user("Administrator")
            frappe.db.set_value("CreditFlow Subscription", self.subscription.name,
                {"current_period_end": add_days(now_datetime(), -1), "cancel_at_period_end": cancelled})
            clear_request_cache(); frappe.set_user(self.owner)
            with self.assertRaises(frappe.PermissionError):
                require_feature(self.business.name, "advanced_reports")

    def test_authorized_reconciliation_exact_match_and_retry(self):
        _, payment = self.checkout()
        record = {"transaction_id": "BANK-" + uuid4().hex, "reference": payment.internal_order_id,
            "amount": payment.amount, "currency": "TND", "direction": "CREDIT", "bank_status": "BOOKED", "transaction_date": now_datetime().date().isoformat(),
            "destination_account": payment.destination_account}
        frappe.set_user("Administrator")
        bank.reconcile_transactions([record], "authorized-statement")
        self.assertEqual(payment.reload().status, "VERIFIED")
        end = self.subscription.reload().current_period_end
        bank.reconcile_transactions([record], "authorized-statement")
        self.assertEqual(self.subscription.reload().current_period_end, end)

    def test_partial_wrong_currency_and_unmatched_bank_records_do_not_activate(self):
        _, payment = self.checkout()
        for changes in ({"amount": 1}, {"currency": "USD"}, {"reference": ""}, {"reference": "unknown"}):
            record = {"transaction_id": uuid4().hex, "reference": payment.internal_order_id,
                "amount": 59, "currency": "TND", "direction": "CREDIT", "bank_status": "BOOKED", "transaction_date": now_datetime().date().isoformat(),
                "destination_account": payment.destination_account, **changes}
            frappe.set_user("Administrator")
            bank.reconcile_transactions([record], "authorized-statement")
            self.assertNotEqual(payment.reload().status, "VERIFIED")
        self.assert_unpaid()

    def test_scheduler_without_source_explicitly_uses_manual_mode(self):
        frappe.set_user("Administrator")
        with patch.dict(frappe.conf, {"creditflow_bank_reconciliation_adapter": None}):
            self.assertEqual(bank.scheduled_reconciliation(), {"mode": "MANUAL"})

    def test_source_failure_and_retry_never_double_activate(self):
        _, payment = self.checkout()
        record = {"transaction_id": uuid4().hex, "reference": payment.internal_order_id,
            "amount": 59, "currency": "TND", "direction": "CREDIT", "bank_status": "BOOKED", "transaction_date": now_datetime().date().isoformat(),
            "destination_account": payment.destination_account}
        frappe.set_user("Administrator")
        original_get_attr = frappe.get_attr
        adapter = "creditflow.tests.authorized_bank_export"
        def resolve(fn):
            return adapter_function if fn == adapter else original_get_attr(fn)
        with patch.dict(frappe.conf, {"creditflow_bank_reconciliation_adapter": adapter}):
            adapter_function = lambda: (_ for _ in ()).throw(TimeoutError("secret-source-token"))
            with patch("creditflow.billing.bank.frappe.get_attr", side_effect=resolve):
                self.assertEqual(bank.scheduled_reconciliation(), {"status": "SOURCE_UNAVAILABLE"})
            self.assertEqual(payment.reload().status, "PENDING_PAYMENT")
            adapter_function = lambda: [record]
            with patch("creditflow.billing.bank.frappe.get_attr", side_effect=resolve):
                bank.scheduled_reconciliation()
                self.assertEqual(payment.reload().status, "VERIFIED")
                end = self.subscription.reload().current_period_end
                bank.scheduled_reconciliation()
                self.assertEqual(self.subscription.reload().current_period_end, end)

    def test_maintenance_expiry_and_reminders_are_retry_safe(self):
        _, payment = self.checkout()
        frappe.set_user("Administrator")
        bank.maintain_payment(payment.name)
        count = frappe.db.count("CreditFlow Billing Audit", {"payment": payment.name, "event": "REVIEW_REMINDER"})
        bank.maintain_payment(payment.name)
        self.assertEqual(frappe.db.count("CreditFlow Billing Audit", {"payment": payment.name, "event": "REVIEW_REMINDER"}), count)
        frappe.db.set_value(payment.doctype, payment.name, "expires_at", add_days(now_datetime(), -1))
        bank.maintain_payment(payment.name)
        self.assertEqual(payment.reload().status, "EXPIRED")
        bank.maintain_payment(payment.name)
        self.assertEqual(payment.reload().status, "EXPIRED")
        bank.maintain_subscription(self.subscription.name)
        self.assertEqual(self.subscription.reload().status, "EXPIRED")
        bank.maintain_subscription(self.subscription.name)
        self.assertEqual(self.subscription.reload().status, "EXPIRED")

    def test_active_pdf_content_is_not_accepted(self):
        _, payment = self.checkout()
        content = base64.b64encode(b"%PDF-1.4\n/JavaScript (alert(1))\n%%EOF").decode()
        with self.assertRaises(frappe.ValidationError):
            bank.submit_proof(payment.name, "receipt.pdf", content)
        self.assert_unpaid()

    def test_owner_cancellation_preserves_access_through_paid_period(self):
        from creditflow.billing.service import request_cancellation
        _, payment = self.checkout(); self.review(payment)
        end = self.subscription.reload().current_period_end
        clear_request_cache(); frappe.set_user(self.owner)
        result = request_cancellation()
        self.assertTrue(result["ok"])
        self.assertEqual(self.subscription.reload().current_period_end, end)
        self.assertEqual(self.subscription.cancel_at_period_end, 1)
        self.assertTrue(require_feature(self.business.name, "advanced_reports"))

    def test_debit_unsettled_or_unconfirmed_source_records_never_activate(self):
        _, payment = self.checkout()
        for changes in ({"direction": "DEBIT"}, {"bank_status": "PENDING"},
                {"direction": None}, {"bank_status": None}):
            record = {"transaction_id": uuid4().hex, "reference": payment.internal_order_id,
                "amount": 59, "currency": "TND", "direction": "CREDIT", "bank_status": "BOOKED",
                "transaction_date": now_datetime().date().isoformat(),
                "destination_account": payment.destination_account, **changes}
            frappe.set_user("Administrator")
            bank.reconcile_transactions([record], "authorized-statement")
            self.assertNotEqual(payment.reload().status, "VERIFIED")
        self.assert_unpaid()

    def test_receipt_cannot_be_public_detached_or_submitted_outside_billing(self):
        from frappe.utils.file_manager import save_file
        _, payment = self.checkout()
        bank.submit_proof(payment.name, "receipt.pdf", pdf_receipt())
        name = frappe.db.get_value("File", {"attached_to_doctype": payment.doctype,
            "attached_to_name": payment.name}, "name")
        self.assertTrue(name)
        for changes in ({"is_private": 0}, {"attached_to_doctype": "Business", "attached_to_name": self.other.name},
                {"attached_to_doctype": None, "attached_to_name": None}):
            file = frappe.get_doc("File", name)
            file.update(changes)
            with self.subTest(changes=changes), self.assertRaises(frappe.PermissionError):
                file.save(ignore_permissions=True)
            persisted = frappe.get_doc("File", name)
            self.assertEqual(persisted.is_private, 1)
            self.assertEqual(persisted.attached_to_doctype, payment.doctype)
            self.assertEqual(persisted.attached_to_name, payment.name)
        with self.assertRaises(frappe.PermissionError):
            save_file("outside-billing.pdf", base64.b64decode(pdf_receipt()), payment.doctype,
                payment.name, is_private=1)
        self.assertEqual(payment.reload().status, "PROOF_SUBMITTED")
        self.assert_unpaid()

    def test_legacy_pending_gateway_history_has_no_bank_checkout_authority(self):
        _, payment = self.checkout()
        frappe.set_user("Administrator")
        legacy = frappe.copy_doc(payment)
        legacy.provider = "KONNECT"
        legacy.status = "PENDING"
        legacy.internal_order_id = "LEGACY-" + uuid4().hex
        legacy.provider_payment_id = "OLD-" + uuid4().hex
        legacy.insert()
        frappe.set_user(self.owner)
        result = reconcile_owner_payment(legacy.name)
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["bank"], {})
        self.assertEqual(legacy.reload().status, "PENDING")
        with self.assertRaises(frappe.ValidationError):
            bank.submit_proof(legacy.name, "receipt.pdf", pdf_receipt())
        self.assert_unpaid()

    def test_png_and_jpeg_receipts_are_private_and_never_activate(self):
        from PIL import Image
        _, payment = self.checkout()
        for extension, image_format in (("png", "PNG"), ("jpeg", "JPEG")):
            stream = BytesIO()
            Image.new("RGB", (12, 12), color="white").save(stream, format=image_format)
            bank.submit_proof(payment.name, "receipt." + extension,
                base64.b64encode(stream.getvalue()).decode())
            self.assertEqual(payment.reload().status, "PROOF_SUBMITTED")
            file = frappe.get_doc("File", {"file_url": payment.receipt})
            self.assertEqual(file.is_private, 1)
            self.assertEqual(file.attached_to_name, payment.name)
            self.assertEqual(file.attached_to_doctype, payment.doctype)
        self.assert_unpaid()
