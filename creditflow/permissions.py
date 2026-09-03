import frappe
from frappe import _

CREDITFLOW_ROLES = {"OWNER", "STAFF"}
ADMINISTRATIVE_ROLES = {"System Manager"}
SYSTEM_LEDGER_DOCTYPES = {"Credit Transaction", "Supplier Transaction", "Stock Movement"}
SYSTEM_LEDGER_MUTATIONS = {"create", "write", "submit", "cancel", "delete", "amend"}

DEFAULTED_DOCTYPES = {
	"Customer",
	"Product",
	"Supplier",
	"Supplier Payment",
	"Supplier Transaction",
	"Sale",
	"Payment",
	"Purchase",
	"Sale Reversal",
	"Sale Return",
	"Onboarding Import Batch",
	"Payment Reversal",
	"Purchase Reversal",
	"Supplier Payment Reversal",
	"CreditFlow Subscription",
	"CreditFlow Billing Payment",
	"Withholding Tax",
	"TEJ Export Batch",
}


def has_cross_business_access(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or bool(ADMINISTRATIVE_ROLES.intersection(frappe.get_roles(user)))


def _is_creditflow_user(user):
	return bool(CREDITFLOW_ROLES.intersection(frappe.get_roles(user)))


def get_user_business(user=None):
	user = user or frappe.session.user
	if has_cross_business_access(user) or not _is_creditflow_user(user):
		return None
	return frappe.db.get_value("User", user, "creditflow_business")


def get_permission_query_conditions(user=None, doctype=None):
	user = user or frappe.session.user
	if has_cross_business_access(user) or not _is_creditflow_user(user):
		return None

	business = get_user_business(user)
	if not business:
		return "1=0"

	field = "name" if doctype == "Business" else "business"
	return f"`tab{doctype}`.`{field}` = {frappe.db.escape(business)}"


def has_permission(doc, ptype=None, user=None, **kwargs):
	user = user or frappe.session.user
	if has_cross_business_access(user):
		return True
	if not _is_creditflow_user(user):
		return False
	if (
		"STAFF" in frappe.get_roles(user)
		and doc.doctype in SYSTEM_LEDGER_DOCTYPES
		and ptype in SYSTEM_LEDGER_MUTATIONS
	):
		return False

	assigned_business = get_user_business(user)
	if not assigned_business:
		return False

	document_business = doc.name if doc.doctype == "Business" else doc.get("business")
	if ptype == "create" and doc.is_new() and not document_business:
		return True
	return document_business == assigned_business


def set_and_validate_business(doc, method=None):
	user = frappe.session.user
	if has_cross_business_access(user) or not _is_creditflow_user(user):
		return

	assigned_business = get_user_business(user)
	if not assigned_business:
		frappe.throw(
			_("Your user is not assigned to a Kivo Business."),
			frappe.PermissionError,
		)

	if doc.doctype == "Business":
		document_business = doc.name
	elif doc.doctype in DEFAULTED_DOCTYPES and doc.is_new() and not doc.get("business"):
		doc.business = assigned_business
		document_business = assigned_business
	else:
		document_business = doc.get("business")

	if document_business != assigned_business:
		frappe.throw(
			_("You can only access records for your assigned Kivo Business."),
			frappe.PermissionError,
		)

	# One centralized gate protects Desk, REST, direct Document calls and generated
	# tenant records. Legacy Businesses are allowed by the subscription policy.
	if doc.doctype not in {"CreditFlow Subscription", "CreditFlow Billing Payment"}:
		from creditflow.subscription import require_write_access

		require_write_access(assigned_business)


def enforce_user_business_limit(doc, method=None):
	"""Enforce max_users for enabled OWNER/STAFF users assigned to a Business."""
	user = frappe.session.user
	if has_cross_business_access(user):
		return
	roles = {row.role for row in (doc.get("roles") or [])}
	eligible = bool(doc.enabled and doc.creditflow_business and CREDITFLOW_ROLES.intersection(roles))
	if not eligible:
		return
	assigned = get_user_business(user)
	if not assigned or doc.creditflow_business != assigned:
		frappe.throw(_("Users can only be assigned to your own Kivo Business."), frappe.PermissionError)
	from creditflow.subscription import get_entitlement, require_write_access

	require_write_access(assigned)
	limit = get_entitlement(assigned, "max_users")
	if limit is None:
		return
	try:
		limit = int(limit)
	except (TypeError, ValueError):
		frappe.throw(_("The max_users entitlement is invalid."))
	count = frappe.db.sql(
		"""SELECT COUNT(DISTINCT user.name)
		FROM `tabUser` user
		INNER JOIN `tabHas Role` role ON role.parent=user.name AND role.parenttype='User'
		WHERE user.enabled=1 AND user.creditflow_business=%s AND user.name!=%s
		AND role.role IN ('OWNER','STAFF')""",
		(assigned, doc.name or ""),
	)[0][0]
	if count + 1 > limit:
		frappe.throw(_("Your Kivo plan allows a maximum of {0} users.").format(limit), frappe.PermissionError)
