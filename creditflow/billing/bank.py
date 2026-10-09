"""Bank transfer billing. No receipt or customer claim is payment authority."""
import base64
import binascii
import hashlib
import io
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime

PAYMENT = "CreditFlow Billing Payment"
TRANSACTION = "CreditFlow Bank Transaction"
AUDIT = "CreditFlow Billing Audit"
OPEN_STATES = {"PENDING_PAYMENT", "PROOF_SUBMITTED", "UNDER_REVIEW"}
DEFAULT_RIB = "2519 0000 0001 5085 8272"
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
_mutation = ContextVar("kivo_bank_mutation", default=False)


@contextmanager
def billing_mutation():
    token = _mutation.set(True)
    try:
        yield
    finally:
        _mutation.reset(token)


def atomic(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        point = "bank_" + frappe.generate_hash(length=12)
        frappe.db.savepoint(point)
        try:
            with billing_mutation():
                return fn(*args, **kwargs)
        except Exception as error:
            # InnoDB removes all savepoints when rolling back a deadlock.
            if isinstance(error, frappe.QueryDeadlockError):
                frappe.db.rollback()
            else:
                frappe.db.rollback(save_point=point)
            from creditflow.subscription import clear_request_cache
            clear_request_cache()
            raise
    return wrapped


def bank_settings():
    settings = frappe.get_single("Kivo Bank Settings")
    return {
        "rib": settings.rib or DEFAULT_RIB,
        "bank_name": settings.bank_name or None,
        "beneficiary_name": settings.beneficiary_name or None,
        "expiry_days": int(settings.payment_expiry_days or 7),
        "review_reminder_days": int(settings.review_reminder_days or 1),
        "expiration_reminder_days": int(settings.expiration_reminder_days or 3),
    }


def account(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def require_admin():
    from creditflow.permissions import has_cross_business_access
    if frappe.session.user == "Guest" or not has_cross_business_access():
        frappe.throw(_("Only authorized billing administrators can review transfers."), frappe.PermissionError)


def lock_payment(name):
    # Always lock Business first, including separate renewals with no subscription link.
    business = frappe.db.get_value(PAYMENT, name, "business")
    if not business:
        frappe.throw(_("Payment not found."), frappe.DoesNotExistError)
    frappe.db.get_value("Business", business, "name", for_update=True)
    return frappe.get_doc(PAYMENT, name, for_update=True)


def owner_payment(name, lock=False):
    from creditflow.billing.service import _owner_business
    business = _owner_business()
    if frappe.db.get_value(PAYMENT, name, "business") != business:
        frappe.throw(_("Billing payment is outside your Business."), frappe.PermissionError)
    return lock_payment(name) if lock else frappe.get_doc(PAYMENT, name)


def audit(payment, event, notes=None, transaction=None):
    with billing_mutation():
        frappe.get_doc({"doctype": AUDIT, "payment": payment.name,
            "business": payment.business, "event": event, "actor": frappe.session.user,
            "occurred_at": now_datetime(), "notes": notes, "bank_transaction": transaction,
        }).insert(ignore_permissions=True)


def save(payment):
    with billing_mutation():
        payment.save(ignore_permissions=True)


def public_payment(payment):
    settings = bank_settings()
    return {key: payment.get(key) for key in ("name", "plan", "amount", "currency", "billing_cycle",
        "status", "internal_order_id", "expires_at", "receipt", "paid_at")} | {
        "payment": payment.name, "bank": ({"rib": payment.destination_account or settings["rib"],
            "bank_name": settings["bank_name"], "beneficiary_name": settings["beneficiary_name"]}
            if payment.provider == "BANK_TRANSFER" else {})}


def validate_receipt(filename, content):
    if not isinstance(filename, str) or len(filename) > 180 or "/" in filename or "\\" in filename:
        frappe.throw(_("Invalid receipt filename."))
    if not isinstance(content, str) or len(content) > ((MAX_RECEIPT_BYTES + 2) // 3) * 4:
        frappe.throw(_("Receipt must be at most 5 MiB."))
    try:
        data = base64.b64decode(content, validate=True)
    except (ValueError, binascii.Error):
        frappe.throw(_("Invalid receipt encoding."))
    if not data or len(data) > MAX_RECEIPT_BYTES:
        frappe.throw(_("Receipt must be at most 5 MiB."))
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension == "pdf":
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
            frappe.throw(_("Invalid PDF receipt."))
        # Active PDF content is never accepted. Receipts remain untrusted documents.
        decoded_names = re.sub(rb"#([0-9A-Fa-f]{2})", lambda match: bytes([int(match.group(1), 16)]), data)
        if re.search(rb"/(?:JavaScript|JS|Launch|EmbeddedFile|OpenAction|AA)\b", decoded_names):
            frappe.throw(_("PDF receipts with active or embedded content are not accepted."))
    elif extension in {"png", "jpg", "jpeg"}:
        from PIL import Image
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != ("PNG" if extension == "png" else "JPEG"):
                    raise ValueError()
                if image.width * image.height > 25000000:
                    raise ValueError()
                image.verify()
        except Exception:
            frappe.throw(_("Invalid image receipt."))
    else:
        frappe.throw(_("Upload a PNG, JPEG or PDF receipt."))
    return data, extension


@atomic
def submit_proof(payment, filename, content):
    from frappe.utils.file_manager import save_file
    doc = owner_payment(payment, lock=True)
    if doc.provider != "BANK_TRANSFER" or doc.status not in OPEN_STATES:
        frappe.throw(_("This payment does not accept receipts."))
    if doc.expires_at and get_datetime(doc.expires_at) <= now_datetime():
        frappe.throw(_("This payment request has expired. Create a new request."))
    data, extension = validate_receipt(filename, content)
    file = save_file("receipt-" + frappe.generate_hash(length=24) + "." + extension,
        data, PAYMENT, doc.name, is_private=1, df="receipt")
    doc.receipt = file.file_url
    # Supplementary evidence must not clear an administrator's review state or
    # a reconciliation mismatch flag. Uploaded evidence never verifies funds.
    if doc.status != "UNDER_REVIEW":
        doc.status = "PROOF_SUBMITTED"
    save(doc)
    audit(doc, "PROOF_SUBMITTED", "Receipt received; funds have not been verified")
    return public_payment(doc)


def guard_file(doc, method=None):
    stored_type = frappe.db.get_value("File", doc.name, "attached_to_doctype") if doc.name else None
    protected = doc.attached_to_doctype == PAYMENT or stored_type == PAYMENT
    if not protected:
        return
    if not _mutation.get():
        frappe.throw(_("Billing receipts must be submitted through billing and cannot be altered."), frappe.PermissionError)
    if not doc.is_private or not doc.attached_to_name:
        frappe.throw(_("Billing receipts must use private storage."), frappe.PermissionError)


class BankReceiptFileMixin:
    def is_downloadable(self):
        # Frappe File's own helper grants read to its original owner before
        # checking the parent. Receipts must follow current tenant assignment.
        if self.attached_to_doctype == PAYMENT:
            from creditflow.permissions import get_user_business, has_cross_business_access
            if not self.is_private or frappe.session.user == "Guest":
                return False
            if has_cross_business_access():
                return True
            business = get_user_business()
            return bool(business and "OWNER" in frappe.get_roles()
                and frappe.db.get_value(PAYMENT, self.attached_to_name, "business") == business)
        return super().is_downloadable()


def guard_record(doc, method=None):
    if not _mutation.get():
        # Retain administrative historical imports and test fixtures, never approvals.
        from creditflow.permissions import has_cross_business_access
        if doc.doctype == PAYMENT and doc.is_new() and has_cross_business_access() and doc.status in {"PENDING", "PENDING_PAYMENT"}:
            return
        frappe.throw(_("Billing records can only be changed through the billing service."), frappe.PermissionError)


def _received_record(record, source):
    from creditflow.billing.service import tnd_to_millimes
    if not isinstance(record, dict):
        frappe.throw(_("Invalid bank record."))
    transaction_id = str(record.get("transaction_id") or "").strip()
    if not transaction_id or len(transaction_id) > 140 or not source or len(source) > 140:
        frappe.throw(_("A bank transaction identifier and source are required."))
    if record.get("currency") != "TND":
        frappe.throw(_("Bank transaction currency must be TND."))
    amount_minor = tnd_to_millimes(record.get("amount"))
    try:
        received = get_datetime(record.get("transaction_date")) if record.get("transaction_date") else None
    except Exception:
        received = None
    if not received or received.date() > now_datetime().date():
        frappe.throw(_("A valid received transaction date is required."))
    # Global transaction ID protection across imports, manual reviews and adapters.
    key = hashlib.sha256(transaction_id.encode()).hexdigest()
    data = {"doctype": TRANSACTION, "transaction_key": key, "transaction_id": transaction_id,
        "source": source, "reference": str(record.get("reference") or "").strip()[:140],
        "amount": amount_minor / 1000, "currency": "TND", "transaction_date": received,
        "destination_account": account(record.get("destination_account")), "status": "UNMATCHED"}
    existing = frappe.db.get_value(TRANSACTION, {"transaction_key": key}, "name")
    if existing:
        tx = frappe.get_doc(TRANSACTION, existing, for_update=True)
        for field in ("transaction_id", "reference", "currency", "destination_account"):
            if (tx.get(field) or "") != (data[field] or ""):
                frappe.throw(_("Conflicting bank transaction data requires investigation."))
        if tnd_to_millimes(tx.amount) != amount_minor or get_datetime(tx.transaction_date) != received:
            frappe.throw(_("Conflicting bank transaction data requires investigation."))
        return tx
    return frappe.get_doc(data).insert(ignore_permissions=True)


def match_error(payment, tx):
    from creditflow.billing.service import tnd_to_millimes
    if tx.reference != payment.internal_order_id:
        return "Missing or incorrect payment reference"
    if tnd_to_millimes(tx.amount) != tnd_to_millimes(payment.amount) or payment.currency != tx.currency:
        return "Incorrect amount or currency"
    day = get_datetime(tx.transaction_date).date()
    if day < get_datetime(payment.created_at or payment.creation).date() or (payment.expires_at and day > get_datetime(payment.expires_at).date()):
        return "Transaction date outside payment window"
    if tx.destination_account and tx.destination_account != account(payment.destination_account):
        return "Incorrect destination account"
    return None


def _verify(payment, tx, notes=None):
    from creditflow.billing.service import _activate
    if payment.status == "VERIFIED":
        if payment.bank_transaction != tx.name:
            frappe.throw(_("Payment already credited using another transaction."))
        audit(payment, "VERIFICATION_REPLAY", "Existing verified payment returned without extending access", tx.name)
        return public_payment(payment)
    if payment.provider != "BANK_TRANSFER" or payment.status not in OPEN_STATES:
        frappe.throw(_("This payment cannot be verified."))
    if tx.payment and tx.payment != payment.name:
        frappe.throw(_("Bank transaction has already been credited."))
    error = match_error(payment, tx)
    if error:
        frappe.throw(_(error))
    _activate(payment, now_datetime())
    payment.status = "VERIFIED"
    payment.paid_at = now_datetime()
    payment.verified_by = frappe.session.user
    payment.bank_transaction = tx.name
    payment.admin_notes = notes
    save(payment)
    tx.payment = payment.name
    tx.status = "MATCHED"
    tx.save(ignore_permissions=True)
    audit(payment, "VERIFIED", notes, tx.name)
    notify(payment, "Payment verified", "Your bank transfer was verified and your Kivo subscription is active.")
    return public_payment(payment)


def log_review_attempt(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as error:
            # Redis deferred Error Log survives the failed request's rollback.
            # Include only identifiers and category, never uploaded/bank content.
            try:
                frappe.log_error(title="Bank payment review denied or failed", defer_insert=True,
                    message=json.dumps({"actor": frappe.session.user,
                        "payment": str(kwargs.get("payment", args[0] if args else ""))[:140],
                        "error_type": type(error).__name__}))
            except Exception:
                pass  # Logging failure must not hide the original denial.
            raise
    return wrapped


@log_review_attempt
@atomic
def admin_review(payment, action, transaction_id=None, amount=None, transaction_date=None,
        reference=None, destination_account=None, notes=None):
    require_admin()
    doc = lock_payment(payment)
    assigned = frappe.db.get_value("User", frappe.session.user, "creditflow_business")
    if doc.requested_by == frappe.session.user or doc.owner == frappe.session.user or assigned == doc.business:
        frappe.throw(_("You cannot review your own Business payment."), frappe.PermissionError)
    if action == "VERIFIED":
        if not isinstance(notes, str) or not notes.strip():
            frappe.throw(_("Record how actual received funds were independently checked."))
        tx = _received_record({"transaction_id": transaction_id, "reference": reference,
            "amount": amount, "currency": "TND", "transaction_date": transaction_date,
            "destination_account": destination_account}, "ADMIN_VERIFIED_BANK_RECORD")
        doc.reviewed_at = now_datetime()
        doc.reviewed_by = frappe.session.user
        return _verify(doc, tx, notes.strip()[:2000])
    if action not in {"UNDER_REVIEW", "REJECTED"} or doc.provider != "BANK_TRANSFER" or doc.status not in OPEN_STATES:
        frappe.throw(_("Invalid payment review transition."))
    if action == "REJECTED" and not notes:
        frappe.throw(_("A rejection reason is required."))
    doc.status = action
    doc.admin_notes = str(notes or "")[:2000]
    doc.reviewed_at = now_datetime()
    doc.reviewed_by = frappe.session.user
    save(doc)
    audit(doc, action, doc.admin_notes)
    if action == "REJECTED":
        notify(doc, "Payment rejected", "Your payment was rejected. Contact Kivo billing for details.")
    return public_payment(doc)


def admin_payment_details(payment):
    require_admin()
    doc = frappe.get_doc(PAYMENT, payment)
    return {"payment": doc.as_dict(), "history": frappe.get_all(PAYMENT,
        filters={"business": doc.business}, fields=["name", "status", "amount", "plan", "paid_at"], order_by="creation desc"),
        "audit": frappe.get_all(AUDIT, filters={"payment": doc.name},
            fields=["event", "actor", "occurred_at", "notes", "bank_transaction"], order_by="creation asc")}


def notify(payment, subject, message):
    # Native Notification Logs are transactional and visible even without SMTP.
    recipients = [payment.requested_by] if payment.requested_by else []
    for recipient in recipients:
        frappe.get_doc({"doctype": "Notification Log", "subject": subject, "email_content": message,
            "for_user": recipient, "type": "Alert", "document_type": PAYMENT,
            "document_name": payment.name}).insert(ignore_permissions=True)


@atomic
def reconcile_transaction(record, source):
    # This entry point is deliberately not whitelisted; only server configured sources.
    require_admin()
    if not isinstance(record, dict) or record.get("direction") != "CREDIT" or record.get("bank_status") != "BOOKED":
        frappe.throw(_("Only independently confirmed booked incoming bank transfers can be reconciled."))
    reference = record.get("reference") if isinstance(record, dict) else None
    names = frappe.get_all(PAYMENT, filters={"internal_order_id": reference, "provider": "BANK_TRANSFER"}, pluck="name") if reference else []
    payment = lock_payment(names[0]) if len(names) == 1 else None
    tx = _received_record(record, source)
    if tx.status == "MATCHED":
        return {"status": "MATCHED", "transaction": tx.name}
    if not payment or match_error(payment, tx) or payment.status not in OPEN_STATES:
        tx.status = "REVIEW_REQUIRED"
        tx.review_reason = match_error(payment, tx) if payment else "Unmatched or ambiguous reference"
        tx.save(ignore_permissions=True)
        if payment and payment.status in OPEN_STATES:
            payment.status = "UNDER_REVIEW"
            save(payment)
            audit(payment, "RECONCILIATION_REVIEW", tx.review_reason, tx.name)
        return {"status": "REVIEW_REQUIRED", "transaction": tx.name}
    return _verify(payment, tx, "Verified against authorized bank source: " + source)


def reconcile_transactions(records, source):
    require_admin()
    results = []
    for record in records:
        try:
            results.append(reconcile_transaction(record, source))
        except frappe.QueryDeadlockError:
            # InnoDB rolled back the entire batch, including earlier records.
            # Fail the job so its authorized source can replay the whole batch.
            raise
        except Exception:
            # Persist no raw source data or exception text (may contain credentials).
            frappe.clear_messages()
            frappe.log_error(title="Bank reconciliation record requires investigation",
                message="A bank record could not be reconciled. No entitlement was granted for this attempt.")
            results.append({"status": "ERROR"})
    return results


def scheduled_reconciliation():
    require_admin()
    adapter = frappe.conf.get("creditflow_bank_reconciliation_adapter")
    if not adapter:
        return {"mode": "MANUAL"}
    # Administrator supplied installed Python function, never a request URL/module.
    if not isinstance(adapter, str) or adapter.split(".")[0] not in frappe.get_installed_apps() or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", adapter):
        frappe.throw(_("Bank adapter must be a vetted function in an installed app."))
    try:
        records = frappe.get_attr(adapter)()
        return reconcile_transactions(records, adapter)
    except frappe.QueryDeadlockError:
        raise
    except Exception:
        frappe.clear_messages()
        frappe.log_error(title="Bank reconciliation source unavailable",
            message="The configured authorized source failed. Payments remain unverified; retry next scheduled run.")
        return {"status": "SOURCE_UNAVAILABLE"}


@atomic
def maintain_payment(name):
    settings = bank_settings()
    doc = lock_payment(name)
    if doc.status not in OPEN_STATES:
        return
    now = now_datetime()
    if doc.expires_at and get_datetime(doc.expires_at) <= now:
        doc.status = "EXPIRED"
        doc.failed_at = now
        save(doc)
        audit(doc, "EXPIRED", "Unpaid request expired; no entitlement change")
        notify(doc, "Payment request expired", "Create a new payment request or contact billing if funds were sent.")
    elif not doc.last_review_notification_at or get_datetime(doc.last_review_notification_at) <= add_days(now, -settings["review_reminder_days"]):
        for user in frappe.get_all("Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent"):
            if frappe.db.get_value("User", user, "enabled"):
                frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert",
                    "subject": "Bank transfer awaiting review: " + doc.internal_order_id,
                    "document_type": PAYMENT, "document_name": doc.name}).insert(ignore_permissions=True)
        doc.last_review_notification_at = now
        save(doc)
        audit(doc, "REVIEW_REMINDER")


@atomic
def maintain_subscription(name):
    doc = frappe.get_doc("CreditFlow Subscription", name, for_update=True)
    now = now_datetime()
    end = doc.trial_end if doc.status == "TRIAL" else doc.current_period_end
    if not end or doc.status not in {"ACTIVE", "TRIAL"}:
        return
    if get_datetime(end) <= now:
        doc.status = "CANCELLED" if doc.cancel_at_period_end else "EXPIRED"
        doc.save(ignore_permissions=True)
        _subscription_notice(doc, "Subscription expired", "Kivo is now read-only. Your historical data is safe.")
    elif get_datetime(end) <= add_days(now, bank_settings()["expiration_reminder_days"]) and doc.expiration_reminded_for != get_datetime(end):
        _subscription_notice(doc, "Subscription ending soon", "Your Kivo access period is ending. Visit billing to renew.")
        doc.expiration_reminded_for = end
        doc.save(ignore_permissions=True)


def _subscription_notice(subscription, subject, message):
    for user in frappe.get_all("User", filters={"creditflow_business": subscription.business, "enabled": 1}, pluck="name"):
        if "OWNER" in frappe.get_roles(user):
            frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": subject,
                "email_content": message, "document_type": "CreditFlow Subscription", "document_name": subscription.name}).insert(ignore_permissions=True)


def daily_maintenance():
    require_admin()
    for name in frappe.get_all(PAYMENT, filters={"provider": "BANK_TRANSFER", "status": ["in", list(OPEN_STATES)]}, pluck="name"):
        _maintain_safely(maintain_payment, name)
    for name in frappe.get_all("CreditFlow Subscription", filters={"status": ["in", ["ACTIVE", "TRIAL"]]}, pluck="name"):
        _maintain_safely(maintain_subscription, name)


def _maintain_safely(maintain, name):
    try:
        maintain(name)
    except frappe.QueryDeadlockError:
        # The atomic operation has rolled back the whole job transaction.
        # Retrying is necessary to restore any earlier successful operations.
        raise
    except Exception:
        frappe.clear_messages()
        frappe.log_error(title="Billing maintenance item requires investigation",
            message="An item failed; other billing maintenance items continue. Retry on the next scheduled run.",
            defer_insert=True)
