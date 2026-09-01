import json
import re
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document


class CreditFlowPlan(Document):
    def validate(self):
        self.plan_code = (self.plan_code or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.plan_code):
            frappe.throw(_("Plan Code must contain only uppercase letters, numbers, and underscores."))
        seen = set()
        for row in self.entitlements or []:
            row.entitlement_key = (row.entitlement_key or "").strip().lower()
            if not row.entitlement_key:
                frappe.throw(_("Entitlement Key is required."))
            if row.entitlement_key in seen:
                frappe.throw(_("Duplicate entitlement: {0}").format(row.entitlement_key))
            seen.add(row.entitlement_key)
            self._validate_entitlement_value(row)

    def _validate_entitlement_value(self, row):
        value = str(row.value or "").strip()
        try:
            if row.value_type == "BOOLEAN":
                if value.lower() not in {"true", "false", "1", "0", "yes", "no"}:
                    raise ValueError
            elif row.value_type == "INTEGER":
                int(value)
            elif row.value_type == "DECIMAL":
                if not Decimal(value).is_finite():
                    raise ValueError
            elif row.value_type == "JSON":
                json.loads(value)
            elif row.value_type != "TEXT":
                raise ValueError
        except (InvalidOperation, TypeError, ValueError, json.JSONDecodeError):
            frappe.throw(_("Invalid {0} value for entitlement {1}.").format(row.value_type, row.entitlement_key))
