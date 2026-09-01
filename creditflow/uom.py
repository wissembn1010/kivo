from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe
from frappe import _


BASE_QTY_PRECISION = Decimal("0.000001")


def to_decimal(value, label, row_index):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw(
            _("{0} in row {1} must be a valid number.").format(
                label,
                row_index,
            )
        )

    if not number.is_finite():
        frappe.throw(
            _("{0} in row {1} must be a finite number.").format(
                label,
                row_index,
            )
        )

    return number


def resolve_uom_snapshot(product, selected_uom, quantity, row_index):
    primary_uom = frappe.db.get_value(
        "Product",
        product,
        "primary_unit",
    )

    if not primary_uom:
        frappe.throw(
            _("Product {0} does not have a Primary Unit.").format(
                product
            )
        )

    uom = selected_uom or primary_uom

    active = frappe.db.get_value(
        "CreditFlow UOM",
        uom,
        "active",
    )

    if active is None:
        frappe.throw(
            _("UOM {0} in row {1} does not exist.").format(
                uom,
                row_index,
            )
        )

    if not active:
        frappe.throw(
            _("UOM {0} in row {1} is inactive.").format(
                uom,
                row_index,
            )
        )

    if uom == primary_uom:
        factor = Decimal("1")
    else:
        factor_value = frappe.db.get_value(
            "Product UOM Conversion",
            {
                "parent": product,
                "parenttype": "Product",
                "parentfield": "uom_conversions",
                "uom": uom,
            },
            "conversion_factor",
        )

        if factor_value is None:
            frappe.throw(
                _(
                    "Product {0} has no conversion from {1} to its "
                    "Primary Unit {2}."
                ).format(
                    product,
                    uom,
                    primary_uom,
                )
            )

        factor = to_decimal(
            factor_value,
            _("Conversion Factor"),
            row_index,
        )

        if factor <= 0:
            frappe.throw(
                _(
                    "Conversion Factor in row {0} must be greater than 0."
                ).format(row_index)
            )

    qty = to_decimal(
        quantity,
        _("Quantity"),
        row_index,
    )

    base_quantity = (qty * factor).quantize(
        BASE_QTY_PRECISION,
        rounding=ROUND_HALF_UP,
    )

    return uom, factor, base_quantity


def get_row_base_quantity(row):
    if row.get("base_quantity") not in (None, ""):
        return Decimal(str(row.base_quantity))

    # Backward compatibility for pre-UOM submitted documents.
    return Decimal(str(row.quantity))
