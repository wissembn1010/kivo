from __future__ import annotations

import hashlib
import secrets

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, get_datetime, get_url, now_datetime, validate_email_address
from frappe.utils.password import check_password

from creditflow.permissions import get_user_business
from creditflow.i18n import normalize_language
from creditflow.subscription import create_trial_subscription, get_default_trial_plan

SIGNUP_RATE_LIMIT = 10
SIGNUP_RATE_WINDOW_SECONDS = 60 * 60
VERIFICATION_ATTEMPT_LIMIT = 20
RESEND_RATE_LIMIT = 5
RESEND_COOLDOWN_SECONDS = 60
TOKEN_LIFETIME_MINUTES = 45
SIGNUP_REDIRECT = "/app/kivo"
ONBOARDING_ROUTE = "/creditflow-onboarding"
GENERIC_SIGNUP_MESSAGE = _("If this email can be registered, a verification message will arrive shortly.")
GENERIC_RESEND_MESSAGE = _("If a pending registration exists, a new verification message will arrive shortly.")


def _clean_rpc_unsupported_fields(unsupported, expected_cmd, error_message):
    """Remove only Frappe's command-routing field from public RPC kwargs."""
    unsupported = dict(unsupported)
    if unsupported.get("cmd") == expected_cmd:
        unsupported.pop("cmd")
    if unsupported:
        frappe.throw(error_message)


def _clean_required(value, label, max_length=140):
    value = (value or "").strip()
    if not value:
        frappe.throw(_("{0} is required.").format(label))
    if len(value) > max_length:
        frappe.throw(_("{0} is too long.").format(label))
    return value


def _validate_signup_data(first_name, last_name, email, business_name, phone=None, country=None):
    first_name = _clean_required(first_name, _("First name"))
    last_name = _clean_required(last_name, _("Last name"))
    business_name = _clean_required(business_name, _("Business name"))
    email = _clean_required(email, _("Email")).lower()
    validate_email_address(email, throw=True)
    phone = (phone or "").strip() or None
    country = (country or "").strip() or None
    if country and not frappe.db.exists("Country", country):
        frappe.throw(_("Country is invalid."))
    return first_name, last_name, email, business_name, phone, country


def _token_hash(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _new_token():
    return secrets.token_urlsafe(32)


def _verification_url(token, language=None):
    language_query = f"&_lang={normalize_language(language)}" if language else ""
    public_base_url = (frappe.conf.get("creditflow_public_base_url") or "").rstrip("/")
    if public_base_url:
        if not public_base_url.startswith("https://") and not frappe.conf.developer_mode:
            frappe.throw(_("Kivo public URLs require HTTPS."))
        return f"{public_base_url}/verify-signup?token={token}{language_query}"
    return get_url(f"/verify-signup?token={token}{language_query}", allow_header_override=False)


def _send_verification_email(email, token, language="en"):
    frappe.sendmail(
        recipients=[email],
        subject=_("Verify your Kivo email", lang=language),
        message=_(
            "<p>Confirm your email to create your Kivo Business and start your 14-day trial.</p>"
            "<p><a href=\"{0}\">Verify email and continue</a></p>"
            "<p>This single-use link expires in {1} minutes.</p>"
        , lang=language).format(_verification_url(token, language), TOKEN_LIFETIME_MINUTES),
        now=True,
    )


def _set_fresh_token(pending, is_resend=False):
    now = now_datetime()
    token = _new_token()
    pending.token_hash = _token_hash(token)
    pending.token_expires_at = add_to_date(now, minutes=TOKEN_LIFETIME_MINUTES)
    pending.last_verification_sent_at = now
    pending.status = "PENDING"
    if is_resend:
        window_start = get_datetime(pending.resend_window_start) if pending.resend_window_start else None
        if not window_start or (now - window_start).total_seconds() >= 3600:
            pending.resend_window_start = now
            pending.resend_count = 0
        pending.resend_count = (pending.resend_count or 0) + 1
    pending.save(ignore_permissions=True)
    return token


def _create_user(first_name, last_name, email, password, language="en"):
    user = frappe.get_doc({
        "doctype": "User", "email": email, "first_name": first_name, "last_name": last_name,
        "enabled": 1, "new_password": password, "send_welcome_email": 0, "user_type": "System User",
        "language": normalize_language(language),
    })
    user.flags.no_welcome_mail = True
    return user.insert(ignore_permissions=True)


def _create_business(business_name, email, phone=None, country=None):
    return frappe.get_doc({
        "doctype": "Business", "business_name": business_name, "email": email, "phone": phone,
        "country": country, "onboarding_status": "PROFILE_PENDING", "onboarded_at": now_datetime(),
    }).insert(ignore_permissions=True)


def _assign_owner(user, business):
    user.creditflow_business = business.name
    user.append_roles("OWNER")
    user.save(ignore_permissions=True)
    frappe.clear_cache(user=user.name)


def create_self_service_tenant(first_name, last_name, email, password, business_name, phone=None, country=None, language="en", **unsupported):
    """Trusted provisioning engine. Public callers must pass through token verification."""
    if unsupported:
        frappe.throw(_("Unsupported signup fields."))
    first_name, last_name, email, business_name, phone, country = _validate_signup_data(
        first_name, last_name, email, business_name, phone, country
    )
    if not password:
        frappe.throw(_("Password is required."))
    if frappe.db.exists("User", email):
        try:
            check_password(email, password)
        except frappe.AuthenticationError:
            frappe.throw(_("Unable to complete signup with these details."))
        business = frappe.db.get_value("User", email, "creditflow_business")
        subscription = business and frappe.db.get_value("CreditFlow Subscription", {"business": business}, "name")
        if business and subscription and "OWNER" in frappe.get_roles(email):
            return {"created": False, "user": email, "business": business, "subscription": subscription, "redirect_to": SIGNUP_REDIRECT}
        frappe.throw(_("Unable to complete signup with these details."))

    savepoint = f"creditflow_signup_{frappe.generate_hash(length=8)}"
    previous_user = frappe.session.user
    frappe.db.savepoint(savepoint)
    try:
        user = _create_user(first_name, last_name, email, password, language)
        business = _create_business(business_name, email, phone, country)
        _assign_owner(user, business)
        frappe.set_user(user.name)
        subscription = create_trial_subscription(business.name, get_default_trial_plan())
        return {"created": True, "user": user.name, "business": business.name, "subscription": subscription.name, "redirect_to": SIGNUP_REDIRECT}
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    finally:
        frappe.set_user(previous_user)


def request_signup(first_name, last_name, email, business_name, phone=None, country=None, **unsupported):
    if unsupported:
        frappe.throw(_("Unsupported signup fields."))
    values = _validate_signup_data(first_name, last_name, email, business_name, phone, country)
    first_name, last_name, email, business_name, phone, country = values
    if frappe.db.exists("User", email) or frappe.db.exists("CreditFlow Pending Signup", {"email": email}):
        return {"ok": True, "message": GENERIC_SIGNUP_MESSAGE}

    pending = frappe.get_doc({
        "doctype": "CreditFlow Pending Signup", "email": email, "first_name": first_name,
        "last_name": last_name, "business_name": business_name, "phone": phone, "country": country,
        "status": "PENDING", "language": normalize_language(getattr(frappe.local, "lang", None)),
    }).insert(ignore_permissions=True)
    token = _set_fresh_token(pending)
    try:
        _send_verification_email(email, token, pending.language)
        sent = True
    except Exception:
        frappe.clear_messages()
        frappe.log_error(title="Kivo verification email delivery failed", message="Verification email delivery failed; no tenant was provisioned.")
        sent = False
    return {"ok": True, "sent": sent, "message": GENERIC_SIGNUP_MESSAGE}


def _pending_for_token(token, lock=False):
    token_digest = _token_hash(token)
    suffix = " FOR UPDATE" if lock else ""
    rows = frappe.db.sql(
        "SELECT name FROM `tabCreditFlow Pending Signup` WHERE token_hash=%s OR consumed_token_hash=%s LIMIT 1" + suffix,
        (token_digest, token_digest),
    )
    return (frappe.get_doc("CreditFlow Pending Signup", rows[0][0]), token_digest) if rows else (None, token_digest)


def inspect_verification_token(token):
    pending, token_digest = _pending_for_token(token)
    if not pending:
        return {"valid": False, "state": "INVALID"}
    if pending.status == "PROVISIONED" and pending.consumed_token_hash == token_digest:
        return {"valid": False, "state": "CONSUMED", "login_url": f"/login?redirect-to={ONBOARDING_ROUTE}"}
    if not pending.token_expires_at or get_datetime(pending.token_expires_at) <= now_datetime():
        return {"valid": False, "state": "EXPIRED"}
    return {"valid": pending.status == "PENDING" and pending.token_hash == token_digest, "state": pending.status}


def verify_and_provision(token, password, **unsupported):
    if unsupported:
        frappe.throw(_("Unsupported verification fields."))
    if not password:
        frappe.throw(_("Password is required."))
    savepoint = f"creditflow_verify_{frappe.generate_hash(length=8)}"
    frappe.db.savepoint(savepoint)
    try:
        pending, token_digest = _pending_for_token(token, lock=True)
        if not pending:
            frappe.throw(_("This verification link is invalid."))
        if pending.status == "PROVISIONED" and pending.consumed_token_hash == token_digest:
            return {"ok": True, "already_verified": True, "login_url": f"/login?redirect-to={ONBOARDING_ROUTE}"}
        if pending.token_hash != token_digest:
            frappe.throw(_("This verification link is invalid."))
        if not pending.token_expires_at or get_datetime(pending.token_expires_at) <= now_datetime():
            pending.status = "EXPIRED"
            pending.save(ignore_permissions=True)
            frappe.throw(_("This verification link has expired."))
        result = create_self_service_tenant(
            pending.first_name, pending.last_name, pending.email, password, pending.business_name,
            pending.phone, pending.country, pending.language,
        )
        pending.reload()
        pending.status = "PROVISIONED"
        pending.consumed_token_hash = token_digest
        pending.token_hash = None
        pending.token_expires_at = None
        pending.provisioned_user = result["user"]
        pending.business = result["business"]
        pending.subscription = result["subscription"]
        pending.save(ignore_permissions=True)
        return {"ok": True, "already_verified": not result["created"], "login_url": f"/login?redirect-to={ONBOARDING_ROUTE}"}
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise


def resend_signup_verification(email):
    email = (email or "").strip().lower()
    if not validate_email_address(email):
        return {"ok": True, "message": GENERIC_RESEND_MESSAGE}
    name = frappe.db.get_value("CreditFlow Pending Signup", {"email": email}, "name")
    if not name or frappe.db.exists("User", email):
        return {"ok": True, "message": GENERIC_RESEND_MESSAGE}
    pending = frappe.get_doc("CreditFlow Pending Signup", name)
    now = now_datetime()
    last_sent = get_datetime(pending.last_verification_sent_at) if pending.last_verification_sent_at else None
    window_start = get_datetime(pending.resend_window_start) if pending.resend_window_start else None
    if last_sent and (now - last_sent).total_seconds() < RESEND_COOLDOWN_SECONDS:
        return {"ok": True, "message": GENERIC_RESEND_MESSAGE}
    if window_start and (now - window_start).total_seconds() < 3600 and (pending.resend_count or 0) >= RESEND_RATE_LIMIT:
        return {"ok": True, "message": GENERIC_RESEND_MESSAGE}
    token = _set_fresh_token(pending, is_resend=True)
    try:
        _send_verification_email(email, token, pending.language)
    except Exception:
        frappe.clear_messages()
    return {"ok": True, "message": GENERIC_RESEND_MESSAGE}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=SIGNUP_RATE_LIMIT, seconds=SIGNUP_RATE_WINDOW_SECONDS, methods="POST")
def signup(first_name, last_name, email, business_name, phone=None, country=None, **unsupported):
    _clean_rpc_unsupported_fields(
        unsupported,
        expected_cmd="creditflow.signup.signup",
        error_message=_("Unsupported signup fields."),
    )
    request_signup(first_name, last_name, email, business_name, phone, country)
    return {"ok": True, "message": GENERIC_SIGNUP_MESSAGE}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=RESEND_RATE_LIMIT, seconds=60 * 60, methods="POST")
def resend_verification(email):
    return resend_signup_verification(email)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=VERIFICATION_ATTEMPT_LIMIT, seconds=60 * 60, methods="POST")
def complete_verification(token, password, **unsupported):
    _clean_rpc_unsupported_fields(
        unsupported,
        expected_cmd="creditflow.signup.complete_verification",
        error_message=_("Unsupported verification fields."),
    )
    return verify_and_provision(token, password)
