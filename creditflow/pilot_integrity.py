"""Schema invariants for Business-scoped catalogue and fiscal identities."""
import hashlib
import re

import frappe


def invoice_series_key(business, year):
    # Keep the internal key bounded and independent of punctuation in Business names.
    digest = hashlib.sha256(business.encode()).hexdigest()
    return f"KIVO-FAC-{digest}-{year}-"


def ensure_scoped_identifiers():
    """Install/upgrade only; DDL commits, so run with application writes stopped."""
    constraints = (
        ("Product", "reference", "business_reference_unique"),
        ("Product", "legacy_product_id", "business_legacy_product_id_unique"),
        ("Sale", "invoice_number", "business_invoice_number_unique"),
    )
    # Preflight every constraint before the first DDL/implicit commit. Blank optional
    # identifiers are absence, not duplicate identifiers. Never rename populated IDs.
    for doctype, field, _ in constraints:
        duplicates = frappe.db.sql(
            f"SELECT business FROM `tab{doctype}` WHERE `{field}` IS NOT NULL "
            f"AND `{field}` != '' GROUP BY business, `{field}` HAVING COUNT(*) > 1 LIMIT 1"
        )
        if duplicates:
            frappe.throw(f"Cannot scope {doctype}.{field}: duplicate identifiers within a Business.")
    for doctype, field in (("Product", "legacy_product_id"), ("Sale", "invoice_number")):
        frappe.db.sql(f"UPDATE `tab{doctype}` SET `{field}` = NULL WHERE `{field}` = ''")
    for doctype, field, index in constraints:
        frappe.db.add_unique(doctype, ["business", field], constraint_name=index)
    seed_invoice_series()


def seed_invoice_series():
    maxima = {}
    rows = frappe.db.sql(
        "SELECT business, invoice_number FROM `tabSale` WHERE invoice_number IS NOT NULL"
    )
    for business, number in rows:
        match = re.fullmatch(r"FAC-([0-9]{4})-([0-9]+)", number.rstrip(), flags=re.IGNORECASE)
        if not match:
            # Historical identifiers outside this format remain untouched and cannot
            # collide with future FAC-year-sequence numbers.
            continue
        key = invoice_series_key(business, match[1])
        maxima[key] = max(maxima.get(key, 0), int(match[2]))
    for key, current in maxima.items():
        frappe.db.sql(
            "INSERT INTO `tabSeries` (name, current) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE current = GREATEST(COALESCE(current, 0), VALUES(current))",
            (key, current),
        )
