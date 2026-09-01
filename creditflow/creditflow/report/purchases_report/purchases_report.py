import frappe
from frappe import _
from creditflow.reporting import business_filter,date_conditions,money,money_col, apply_period

def execute(filters=None):
    filters=apply_period(frappe._dict(filters or {})); business=business_filter(filters); values={}; c=["purchase.docstatus=1"]
    if business:c.append("purchase.business=%(business)s");values["business"]=business
    c+=date_conditions(filters,"purchase.business_date",values)
    if filters.get("supplier"):c.append("purchase.supplier=%(supplier)s");values["supplier"]=filters.supplier
    rows=frappe.db.sql(f"""SELECT purchase.business_date purchase_date,purchase.name purchase,purchase.supplier,supplier.supplier_name,purchase.total_amount,CASE WHEN rev.name IS NULL THEN purchase.total_amount-COALESCE(purchase.paid_amount,0) ELSE 0 END supplier_debt_effect,CASE WHEN rev.name IS NULL THEN 'Active' ELSE 'Reversed' END reversal_status FROM `tabPurchase` purchase LEFT JOIN `tabSupplier` supplier ON supplier.name=purchase.supplier LEFT JOIN `tabPurchase Reversal` rev ON rev.original_purchase=purchase.name AND rev.docstatus=1 WHERE {' AND '.join(c)} ORDER BY purchase.business_date,purchase.creation""",values,as_dict=True)
    for row in rows:row.total_amount=money(row.total_amount);row.supplier_debt_effect=money(row.supplier_debt_effect)
    return [{"fieldname":"purchase_date","label":_("Purchase Date"),"fieldtype":"Date"},{"fieldname":"purchase","label":_("Purchase"),"fieldtype":"Link","options":"Purchase"},{"fieldname":"supplier_name","label":_("Supplier"),"fieldtype":"Data","width":180},money_col("total_amount","Total Amount"),money_col("supplier_debt_effect","Net Supplier Debt"),{"fieldname":"reversal_status","label":_("Status"),"fieldtype":"Data"}],rows
