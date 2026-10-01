"""Display-language contracts: canonical report and transaction values stay stable."""
import csv
import re
from collections import Counter
from pathlib import Path
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.translate import clear_cache

from creditflow.creditflow.report.stock_on_hand.stock_on_hand import execute
from creditflow.onboarding import preview_csv


class TestFrenchLocalization(IntegrationTestCase):
    def setUp(self):
        self.language = frappe.local.lang
        frappe.set_user("Administrator")
        clear_cache()

    def tearDown(self):
        frappe.local.lang = self.language

    def test_display_labels_and_context_leave_canonical_values_intact(self):
        frappe.local.lang = "fr"
        for source, expected in {"Business": "Entreprise", "Show Zero Stock": "Afficher les stocks à zéro",
                "Current Stock": "Stock actuel", "Negative": "Négatif", "CASH": "Espèces",
                "Submit": "Valider", "Available Credit": "Crédit disponible"}.items():
            self.assertEqual(frappe._(source), expected)
        self.assertEqual(frappe._("CASH", context="Sale Mode"), "Au comptant")
        options = frappe.get_meta("Sale").get_field("sale_mode").options.splitlines()
        self.assertEqual(options, ["CASH", "CREDIT", "MIXED"])
        self.assertIn("CASH", frappe.get_meta("Payment").get_field("payment_method").options.splitlines())
        frappe.local.lang = "en"
        self.assertEqual(frappe._("Current Stock"), "Current Stock")
        self.assertEqual(frappe._("CASH", context="Sale Mode"), "CASH")

    def test_catalog_placeholders_and_unique_contexts(self):
        path = Path(frappe.get_app_path("creditflow", "translations", "fr.csv"))
        keys = set()
        for row in csv.reader(path.open()):
            key = (row[0], row[2] if len(row) > 2 else "")
            self.assertNotIn(key, keys)
            keys.add(key)
            self.assertEqual(Counter(re.findall(r"\{\d+\}", row[0])), Counter(re.findall(r"\{\d+\}", row[1])), row[0])

    def test_stock_report_language_changes_only_column_labels(self):
        suffix = uuid4().hex[:10]
        business = frappe.get_doc({"doctype": "Business", "business_name": "Localization " + suffix}).insert()
        product = frappe.get_doc({"doctype": "Product", "business": business.name,
            "product_name": "Localization product", "reference": suffix, "primary_unit": "UNIT"}).insert()
        filters = {"business": business.name, "product": product.name, "show_zero_stock": 1}
        frappe.local.lang = "en"
        en_columns, en_rows = execute(filters)
        frappe.local.lang = "fr"
        fr_columns, fr_rows = execute(filters)
        self.assertEqual(en_rows, fr_rows)
        self.assertEqual([c["fieldname"] for c in en_columns], [c["fieldname"] for c in fr_columns])
        self.assertIn("Current Stock", [c["label"] for c in en_columns])
        self.assertIn("Stock actuel", [c["label"] for c in fr_columns])
        self.assertEqual(fr_rows[0]["stock_status"], "Out of Stock")
        self.assertEqual(execute({**filters, "show_negative_stock_only": 1})[1], [])

    def test_import_feedback_language_without_translating_csv_schema(self):
        business = frappe.get_doc({"doctype": "Business", "business_name": "Localization " + uuid4().hex[:10]}).insert()
        frappe.local.lang = "fr"
        preview = preview_csv("CUSTOMERS", "customer_name,credit_limit\n,0\n", business.name)
        self.assertEqual(preview["import_type"], "CUSTOMERS")
        self.assertEqual(preview["invalid_rows"][0]["error"], "Le nom du client est obligatoire.")
        frappe.local.lang = "en"
        self.assertEqual(preview_csv("CUSTOMERS", "customer_name,credit_limit\n,0\n", business.name)["invalid_rows"][0]["error"], "customer_name is required")

    def test_cash_invoice_uses_display_language_without_localizing_snapshot(self):
        import json
        business = frappe.get_doc({"doctype": "Business", "business_name": "Invoice localization " + uuid4().hex[:10]}).insert()
        product = frappe.get_doc({"doctype": "Product", "business": business.name, "product_name": "Invoice product", "reference": uuid4().hex[:10], "primary_unit": "UNIT", "selling_price": 10}).insert()
        # Stock is a prerequisite for a real submitted cash invoice.
        movement = frappe.get_doc({"doctype": "Stock Movement", "business": business.name, "product": product.name, "direction": "IN", "quantity": 2, "movement_reason": "MANUAL_ADJUSTMENT", "business_date": "2026-10-01"}).insert()
        movement.submit()
        frappe.local.lang = "fr"
        sale = frappe.get_doc({"doctype": "Sale", "business": business.name, "sale_mode": "CASH", "amount_paid": 10, "payment_method": "CASH", "business_date": "2026-10-01", "items": [{"product": product.name, "quantity": 1, "unit_price": 10}]}).insert()
        sale.submit()
        self.assertEqual(sale.reload().invoice_customer_name, "Walk-in Customer")
        template = json.loads(Path(frappe.get_app_path("creditflow", "creditflow", "print_format", "fiscal_invoice", "fiscal_invoice.json")).read_text())["html"]
        french = frappe.render_template(template, {"doc": sale})
        self.assertIn("Client de passage", french)
        self.assertNotIn("Walk-in Customer", french)
        frappe.local.lang = "en"
        english = frappe.render_template(template, {"doc": sale})
        self.assertIn("Walk-in Customer", english)
        self.assertEqual(sale.reload().invoice_customer_name, "Walk-in Customer")
