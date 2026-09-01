import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


DEFAULT_UOMS = (
    ("UNIT", "Unit / Piece"),
    ("KG", "Kilogram"),
    ("M", "Meter"),
    ("L", "Liter"),
    ("BOX", "Box"),
    ("PACK", "Pack"),
    ("ROLL", "Roll"),
    ("OTHER", "Other"),
)

DEFAULT_PLANS = (("Starter", "STARTER"), ("Business", "BUSINESS"), ("Pro", "PRO"))


def seed_default_uoms():
    for code, name in DEFAULT_UOMS:
        if frappe.db.exists("CreditFlow UOM", code):
            continue

        frappe.get_doc(
            {
                "doctype": "CreditFlow UOM",
                "uom_code": code,
                "uom_name": name,
                "active": 1,
            }
        ).insert(ignore_permissions=True)


def ensure_security_metadata():
    for role_name in ("OWNER", "STAFF"):
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1, "is_custom": 1}).insert(ignore_permissions=True)
    create_custom_fields({"User": [{"fieldname": "creditflow_business", "label": "CreditFlow Business", "fieldtype": "Link", "options": "Business", "insert_after": "username", "module": "CreditFlow"}]}, update=True)

def seed_default_plans():
    for plan_name, plan_code in DEFAULT_PLANS:
        if not frappe.db.exists("CreditFlow Plan", plan_code):
            frappe.get_doc({"doctype":"CreditFlow Plan","plan_name":plan_name,"plan_code":plan_code,"enabled":1}).insert(ignore_permissions=True)

def after_install():
    ensure_security_metadata()
    seed_default_uoms()
    seed_default_plans()
