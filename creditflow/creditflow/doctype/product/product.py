# Copyright (c) 2026, Wissen Benali and contributors
# For license information, please see license.txt

from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document


class Product(Document):
    def validate(self):
        self.validate_tva_rate()
        self.validate_minimum_stock()
        self.validate_primary_uom()
        self.validate_uom_conversions()

    def validate_tva_rate(self):
        try:
            rate = Decimal(str(self.tva_rate or 0))
        except (InvalidOperation, TypeError, ValueError):
            frappe.throw(_("TVA Rate must be a valid number."))
        if not rate.is_finite() or rate < 0 or rate > 100:
            frappe.throw(_("TVA Rate must be between 0 and 100."))
        self.tva_rate = rate

    def validate_minimum_stock(self):
        try:
            minimum = Decimal(str(self.minimum_stock or 0))
        except (InvalidOperation, TypeError, ValueError):
            frappe.throw(_("Minimum Stock must be a valid number."))
        if not minimum.is_finite() or minimum < 0:
            frappe.throw(_("Minimum Stock must be greater than or equal to 0."))
        self.minimum_stock = minimum

    def validate_primary_uom(self):
        if not self.primary_unit:
            frappe.throw(_("Primary Unit is required."))

        stored = frappe.db.get_value(
            "Product", self.name, "primary_unit", as_dict=True, cache=False
        ) if self.name else None
        if (
            stored
            and stored.primary_unit != self.primary_unit
            and frappe.db.exists("Stock Movement", {"product": self.name})
        ):
            frappe.throw(_("Primary Unit cannot be changed after stock history exists."))

        active = frappe.db.get_value(
            "CreditFlow UOM",
            self.primary_unit,
            "active",
        )

        if active is None:
            frappe.throw(
                _("Primary Unit {0} does not exist.").format(
                    self.primary_unit
                )
            )

        if not active:
            frappe.throw(
                _("Primary Unit {0} is inactive.").format(
                    self.primary_unit
                )
            )

    def validate_uom_conversions(self):
        seen = set()

        for row in self.uom_conversions or []:
            if not row.uom:
                frappe.throw(
                    _("UOM is required in conversion row {0}.").format(
                        row.idx
                    )
                )

            if row.uom == self.primary_unit:
                frappe.throw(
                    _(
                        "Conversion row {0} must not repeat the Primary Unit. "
                        "The Primary Unit always has conversion factor 1."
                    ).format(row.idx)
                )

            if row.uom in seen:
                frappe.throw(
                    _("Duplicate UOM {0} in conversion rows.").format(
                        row.uom
                    )
                )
            seen.add(row.uom)

            active = frappe.db.get_value(
                "CreditFlow UOM",
                row.uom,
                "active",
            )

            if active is None:
                frappe.throw(
                    _("UOM {0} does not exist.").format(row.uom)
                )

            if not active:
                frappe.throw(
                    _("UOM {0} is inactive.").format(row.uom)
                )

            try:
                factor = Decimal(str(row.conversion_factor))
            except (InvalidOperation, TypeError, ValueError):
                frappe.throw(
                    _(
                        "Conversion Factor in row {0} must be a valid number."
                    ).format(row.idx)
                )

            if not factor.is_finite():
                frappe.throw(
                    _(
                        "Conversion Factor in row {0} must be a finite number."
                    ).format(row.idx)
                )

            if factor <= 0:
                frappe.throw(
                    _(
                        "Conversion Factor in row {0} must be greater than 0."
                    ).format(row.idx)
                )
