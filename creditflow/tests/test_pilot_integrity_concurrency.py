"""Committed overlapping transactions. Dedicated synthetic test site ONLY."""
from threading import Event, Thread
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.pilot_integrity import invoice_series_key


class IntegrationTestPilotIntegrityConcurrency(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        if frappe.local.site != "kivo-fix001-tests.localhost":
            self.skipTest("Committed fixtures require the dedicated synthetic site")
        frappe.set_user("Administrator")
        self.token = uuid4().hex[:12]
        self.businesses = []
        self.primary = frappe.local.db
        self.addCleanup(self.cleanup)
        for label in ("A", "B"):
            b = frappe.get_doc({"doctype": "Business", "business_name": f"Pilot race {label} {self.token}"}).insert().name
            self.businesses.append(b)
        self.products = []
        for business in self.businesses:
            product = self.product(business, f"stock-{business}").insert()
            self.products.append(product.name)
            movement = frappe.get_doc({"doctype": "Stock Movement", "business": business,
                "product": product.name, "direction": "IN", "quantity": 10,
                "movement_reason": "OPENING_STOCK", "business_date": frappe.utils.today()}).insert()
            movement.submit()
        self.primary.commit()

    def cleanup(self):
        frappe.local.db = self.primary
        self.primary.rollback()
        frappe.set_user("Administrator")
        for dt in ("Credit Transaction", "Stock Movement", "Sale", "Product"):
            names = frappe.get_all(dt, filters={"business": ["in", self.businesses]}, pluck="name")
            for field in frappe.get_meta(dt).get_table_fields():
                if names:
                    frappe.db.delete(field.options, {"parent": ["in", names], "parenttype": dt})
            if names:
                frappe.db.delete(dt, {"name": ["in", names]})
        for business in self.businesses:
            self.primary.sql("DELETE FROM `tabSeries` WHERE name=%s", invoice_series_key(business, "2026"))
            frappe.db.delete("Business", {"name": business})
        self.primary.commit()

    def product(self, business, reference, legacy=None):
        return frappe.get_doc({"doctype": "Product", "business": business,
            "product_name": "Race product", "reference": reference, "legacy_product_id": legacy})

    def sale(self, index=0):
        return frappe.get_doc({"doctype": "Sale", "business": self.businesses[index],
            "business_date": "2026-09-01", "sale_mode": "CASH", "amount_paid": 1,
            "payment_method": "CASH", "items": [{"product": self.products[index],
            "quantity": 1, "base_price": 1, "unit_price": 1}]}).insert()

    def overlap(self, winner_call, contender_call, *, waits=True, snapshot_isolation=None):
        self.primary.commit()
        ready, proceed, attempting = Event(), Event(), Event()
        results, ids, settings = [], [self.primary.sql("SELECT CONNECTION_ID()")[0][0]], []
        site, sites_path = frappe.local.site, frappe.local.sites_path

        def run():
            try:
                frappe.init(site=site, sites_path=sites_path); frappe.connect(); frappe.set_user("Administrator")
                if snapshot_isolation is not None:
                    frappe.db.sql("SET SESSION innodb_snapshot_isolation=%s", snapshot_isolation)
                ids.append(frappe.db.sql("SELECT CONNECTION_ID()")[0][0])
                settings.append(frappe.db.sql("SELECT @@tx_isolation, @@innodb_snapshot_isolation")[0])
                # The old view exists BEFORE the winning transaction writes.
                frappe.db.sql("SELECT name FROM `tabProduct` WHERE business=%s", self.businesses[0])
                ready.set()
                if not proceed.wait(15): raise TimeoutError("Winner did not proceed")
                attempting.set()
                results.append(contender_call())
                frappe.db.commit()
            except Exception as exc:
                frappe.db.rollback(); results.append(exc)
            finally:
                ready.set(); frappe.destroy()

        thread = Thread(target=run); thread.start()
        try:
            self.assertTrue(ready.wait(15))
            winner = winner_call()
            proceed.set(); self.assertTrue(attempting.wait(15))
            thread.join(0.25 if waits else 10)
            self.assertEqual(thread.is_alive(), waits, "Unexpected Business/unique-index blocking")
            self.primary.commit()
        finally:
            proceed.set(); self.primary.rollback(); thread.join(20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(len(results), 1)
        self.primary.rollback()
        print(f"PILOT RACE: connection_ids={ids}, settings={settings}, waited={waits}, result={type(results[0]).__name__}")
        return winner, results[0]

    def test_product_reference_and_legacy_races(self):
        for field in ("reference", "legacy_product_id"):
            with self.subTest(field=field):
                key = f"race-{field}-{self.token}"
                reference = key if field == "reference" else f"winner-{key}"
                legacy = key if field == "legacy_product_id" else None
                winner = self.product(self.businesses[0], reference, legacy)
                other_reference = key if field == "reference" else f"contender-{key}"
                _, error = self.overlap(winner.insert,
                    lambda: self.product(self.businesses[0], other_reference, legacy).insert())
                self.assertIsInstance(error, frappe.UniqueValidationError)
                self.assertEqual(frappe.db.count("Product", {"business": self.businesses[0], field: key}), 1)
                other = self.product(self.businesses[1], reference, legacy).insert()
                self.assertNotEqual(other.name, winner.name)
                self.primary.commit()

    def test_invoice_same_business_overlapping_submissions(self):
        winner, contender = self.sale(), self.sale()
        name = contender.name
        _, result = self.overlap(winner.submit, lambda: frappe.get_doc("Sale", name).submit().invoice_number,
            snapshot_isolation=0)
        self.assertEqual(result, "FAC-2026-00002")
        winner.reload()
        self.assertEqual(winner.invoice_number, "FAC-2026-00001")
        self.assertEqual(frappe.db.count("Sale", {"business": self.businesses[0], "docstatus": 1}), 2)
        self.assertEqual(frappe.db.count("Stock Movement", {"business": self.businesses[0], "direction": "OUT", "docstatus": 1}), 2)

    def test_invoice_default_snapshot_settings_safe_commit_or_retry(self):
        winner, contender = self.sale(), self.sale()
        name = contender.name
        _, result = self.overlap(winner.submit, lambda: frappe.get_doc("Sale", name).submit().invoice_number)
        if isinstance(result, Exception):
            # MariaDB snapshot isolation can abort a current read after a wait.
            # A fresh whole-transaction retry must allocate exactly the next number.
            self.assertIsInstance(result, frappe.QueryDeadlockError)
            self.assertEqual(result.args[0].args[0], 1020)
            self.assertEqual(frappe.db.get_value("Sale", name, "docstatus"), 0)
            self.assertEqual(frappe.db.count("Stock Movement", {"source_sale": name}), 0)
            retry = frappe.get_doc("Sale", name); retry.submit(); self.primary.commit()
            result = retry.invoice_number
        self.assertEqual(result, "FAC-2026-00002")
        self.assertEqual(frappe.db.count("Sale", {"business": self.businesses[0], "docstatus": 1}), 2)

    def test_invoice_other_business_does_not_wait_or_consume_sequence(self):
        winner, contender = self.sale(), self.sale(1)
        name = contender.name
        # Existing keys isolate row locking from InnoDB's missing-key gap locks.
        for business in self.businesses:
            self.primary.sql("INSERT INTO `tabSeries` (name, current) VALUES (%s, 0)",
                invoice_series_key(business, "2026"))
        self.primary.commit()
        # Isolate the allocator: full posting also uses upstream global naming
        # series for Stock Movement and can wait independently of fiscal numbering.
        def allocate():
            doc = frappe.get_doc("Sale", name)
            doc.assign_invoice_number()
            return doc.invoice_number
        _, result = self.overlap(winner.assign_invoice_number, allocate, waits=False)
        self.assertEqual(result, "FAC-2026-00001")
        self.assertEqual(winner.invoice_number, result)
        for business in self.businesses:
            self.assertEqual(self.primary.sql("SELECT current FROM `tabSeries` WHERE name=%s",
                invoice_series_key(business, "2026"))[0][0], 1)
