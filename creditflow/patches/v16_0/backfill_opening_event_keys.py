"""Protect prior successful imports without modifying their economic postings."""
import frappe

from creditflow.onboarding import FINANCIAL, _created_doctype, opening_event_key


def execute():
    rows = frappe.db.sql("""
        SELECT r.name, r.import_entity, r.created_doctype, r.created_document, b.business
        FROM `tabOnboarding Import Result` r
        JOIN `tabOnboarding Import Batch` b ON b.name = r.parent
        WHERE b.status = 'SUCCESS' AND r.result_status = 'SUCCESS'
          AND (r.economic_key IS NULL OR r.economic_key = '')
        ORDER BY b.creation, r.idx
    """, as_dict=True)
    for row in rows:
        if row.import_entity not in FINANCIAL or row.created_doctype != _created_doctype(row.import_entity):
            continue
        doc = frappe.get_doc(row.created_doctype, row.created_document)
        if doc.business != row.business or doc.docstatus != 1:
            frappe.throw("Opening import audit does not match its posted Business record.")
        data = doc.as_dict()
        data["date"] = doc.get("business_date") or doc.get("transaction_date")
        key = opening_event_key(row.business, row.import_entity, data)
        # Historical duplicates remain intact; one representative reserves the key.
        if not frappe.db.exists("Onboarding Import Result", {"economic_key": key}):
            frappe.db.set_value("Onboarding Import Result", row.name, "economic_key", key, update_modified=False)
