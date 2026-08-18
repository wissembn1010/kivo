import frappe
from frappe import _

CREDITFLOW_ROLES = {"OWNER", "STAFF"}
ADMINISTRATIVE_ROLES = {"System Manager"}
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
	"Payment Reversal",
	"Purchase Reversal",
	"Supplier Payment Reversal",
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
	if has_cross_business_access(user) or not _is_creditflow_user(user):
		return True

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
			_("Your user is not assigned to a CreditFlow Business."),
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
			_("You can only access records for your assigned CreditFlow Business."),
			frappe.PermissionError,
		)
