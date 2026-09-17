# Copyright (c) 2026, Wissen Benali and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from creditflow.permissions import has_cross_business_access


def has_permission(doc, ptype=None, user=None, **kwargs):
	# A deny-only hook: normal role permissions still govern reads and admin access.
	return ptype in {None, "read", "select", "print", "email", "report", "export"} or has_cross_business_access(user)


class CreditFlowUOM(Document):
	def _require_platform_authority(self):
		if not has_cross_business_access():
			frappe.throw(_("Only platform administrators can modify shared units of measure."), frappe.PermissionError)

	def db_insert(self, *args, **kwargs):
		self._require_platform_authority()
		return super().db_insert(*args, **kwargs)

	def db_update(self, *args, **kwargs):
		self._require_platform_authority()
		return super().db_update(*args, **kwargs)

	def before_change(self):
		self._require_platform_authority()

	def before_rename(self, old, new, merge=False):
		self._require_platform_authority()

	def on_trash(self):
		self._require_platform_authority()
