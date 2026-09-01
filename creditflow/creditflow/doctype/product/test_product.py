# Copyright (c) 2026, Wissen Benali and Contributors
# See license.txt

from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestProduct(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        suffix = uuid4().hex[:10]

        self.business = frappe.get_doc(
            {
                "doctype": "Business",
                "business_name": f"Product UOM Test Business {suffix}",
            }
        ).insert()

        self.suffix = suffix

    def tearDown(self):
        frappe.set_user("Administrator")

    def make_product(self, **kwargs):
        suffix = uuid4().hex[:10]

        values = {
            "doctype": "Product",
            "business": self.business.name,
            "product_name": f"Product UOM Test {suffix}",
            "reference": f"PRODUCT-UOM-{suffix}",
            "primary_unit": "UNIT",
        }
        values.update(kwargs)

        return frappe.get_doc(values)

    def test_valid_primary_uom_succeeds(self):
        product = self.make_product(primary_unit="KG")
        product.insert()

        self.assertEqual(product.primary_unit, "KG")

    def test_inactive_primary_uom_is_rejected(self):
        frappe.db.set_value(
            "CreditFlow UOM",
            "OTHER",
            "active",
            0,
        )

        try:
            product = self.make_product(primary_unit="OTHER")

            with self.assertRaises(frappe.ValidationError):
                product.insert()
        finally:
            frappe.db.set_value(
                "CreditFlow UOM",
                "OTHER",
                "active",
                1,
            )

    def test_valid_alternate_conversion_succeeds(self):
        product = self.make_product()

        product.append(
            "uom_conversions",
            {
                "uom": "BOX",
                "conversion_factor": "100",
            },
        )

        product.insert()

        self.assertEqual(
            Decimal(str(product.uom_conversions[0].conversion_factor)),
            Decimal("100"),
        )

    def test_zero_conversion_factor_is_rejected(self):
        product = self.make_product()

        product.append(
            "uom_conversions",
            {
                "uom": "BOX",
                "conversion_factor": "0",
            },
        )

        with self.assertRaises(frappe.ValidationError):
            product.insert()

    def test_negative_conversion_factor_is_rejected(self):
        product = self.make_product()

        product.append(
            "uom_conversions",
            {
                "uom": "BOX",
                "conversion_factor": "-5",
            },
        )

        with self.assertRaises(frappe.ValidationError):
            product.insert()

    def test_duplicate_conversion_uom_is_rejected(self):
        product = self.make_product()

        product.append(
            "uom_conversions",
            {
                "uom": "BOX",
                "conversion_factor": "100",
            },
        )

        product.append(
            "uom_conversions",
            {
                "uom": "BOX",
                "conversion_factor": "120",
            },
        )

        with self.assertRaises(frappe.ValidationError):
            product.insert()

    def test_primary_uom_conversion_row_is_rejected(self):
        product = self.make_product(primary_unit="UNIT")

        product.append(
            "uom_conversions",
            {
                "uom": "UNIT",
                "conversion_factor": "1",
            },
        )

        with self.assertRaises(frappe.ValidationError):
            product.insert()
