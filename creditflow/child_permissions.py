"""Persisted-parent authorization for Kivo child tables.

Database SQL helpers remain trusted server primitives. Document persistence and
parent saves enforce this guard; no permission is derived from parent_doc/flags.
"""

import frappe
from frappe import _

from creditflow.permissions import get_user_business, has_cross_business_access

CHILD_RELATIONSHIPS = {
	"Sale Item": ("Sale", "items"),
	"Purchase Item": ("Purchase", "items"),
	"Sale Return Item": ("Sale Return", "items"),
	"Product UOM Conversion": ("Product", "uom_conversions"),
	"TEJ Export Batch Item": ("TEJ Export Batch", "records"),
	"Onboarding Import Result": ("Onboarding Import Batch", "created_records"),
	"CreditFlow Plan Entitlement": ("CreditFlow Plan", "entitlements"),
}
PARENT_FIELDS = ("parent", "parenttype", "parentfield")
READ_PERMISSIONS = {"read", "select", "print", "email"}


def _deny():
	frappe.throw(
		_("You cannot access or move this child record under the requested parent."), frappe.PermissionError
	)


def _relationship(doc):
	return tuple(doc.get(field) for field in PARENT_FIELDS)


def _authorize_parent(child_type, relation, user, ptype, *, lock, parent_doc=None, check_role=True):
	name, doctype, fieldname = relation
	if not name or (doctype, fieldname) != CHILD_RELATIONSHIPS[child_type]:
		_deny()
	meta = frappe.get_meta(doctype)
	field = meta.get_field(fieldname)
	if not field or field.fieldtype not in frappe.model.table_fields or field.options != child_type:
		_deny()
	fields = ["name", "business"] if meta.has_field("business") else ["name"]
	# Parent first, then child. REST/delete may already hold the child lock;
	# do not wait for a parent lock in that case (fail/retry, not a lock cycle).
	stored = frappe.db.get_value(
		doctype, name, fields, as_dict=True, cache=False, for_update=lock, wait=False
	)
	if stored is None:
		# Parent save validates children before inserting a genuinely new parent.
		if (
			not parent_doc
			or parent_doc.name != name
			or parent_doc.doctype != doctype
			or not parent_doc.is_new()
		):
			_deny()
		stored = parent_doc
	if not has_cross_business_access(user):
		if meta.has_field("business"):
			business = get_user_business(user)
			if not business or stored.business != business:
				_deny()
		elif ptype not in READ_PERMISSIONS:
			_deny()
	if check_role and not has_cross_business_access(user):
		permission = ptype if ptype in READ_PERMISSIONS else "write"
		if not frappe.has_permission(doctype, permission, doc=name, user=user):
			_deny()


def authorize_child(doc, ptype="write", user=None, *, parent_doc=None, check_role=True):
	"""Check the stored relationship, then the destination; never trust __islocal."""
	user = user or frappe.session.user
	lock = ptype not in READ_PERMISSIONS
	incoming = _relationship(doc)
	stored = (
		frappe.db.get_value(doc.doctype, doc.name, PARENT_FIELDS, as_dict=True, cache=False)
		if doc.name
		else None
	)
	original = _relationship(stored) if stored else incoming
	_authorize_parent(
		doc.doctype, original, user, ptype, lock=lock, parent_doc=parent_doc, check_role=check_role
	)
	if stored and lock:
		locked = frappe.db.get_value(
			doc.doctype, doc.name, PARENT_FIELDS, as_dict=True, cache=False, for_update=True
		)
		if locked is None or _relationship(locked) != original:
			_deny()
	if original != incoming:
		_authorize_parent(
			doc.doctype, incoming, user, ptype, lock=lock, parent_doc=parent_doc, check_role=check_role
		)
		if not has_cross_business_access(user):
			_deny()


def validate_child_rows(doc, method=None):
	"""Reject foreign embedded row IDs before the parent or its tables are written."""
	for child_type, (parent_type, fieldname) in CHILD_RELATIONSHIPS.items():
		if doc.doctype != parent_type:
			continue
		for row in doc.get(fieldname) or []:
			if row.doctype != child_type or _relationship(row) != (doc.name, parent_type, fieldname):
				_deny()
			# Parent save owns role authorization, including controlled internal
			# imports. The child still independently enforces persisted tenancy.
			authorize_child(row, parent_doc=doc, check_role=False)


def validate_child_change(doc, method=None):
	authorize_child(doc, check_role=False)


class ChildAuthorizationMixin:
	def has_permission(self, permtype="read", *, debug=False, user=None):
		try:
			authorize_child(self, permtype, user)
		except frappe.PermissionError:
			return False
		return super().has_permission(permtype, debug=debug, user=user)

	def db_insert(self, *args, **kwargs):
		authorize_child(self, check_role=False)
		return super().db_insert(*args, **kwargs)

	def db_update(self, *args, **kwargs):
		authorize_child(self, check_role=False)
		return super().db_update(*args, **kwargs)
