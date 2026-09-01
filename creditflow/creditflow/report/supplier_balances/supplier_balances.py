import frappe
from frappe import _
from frappe.utils import cint
from creditflow.reporting import business_filter,money,money_col

def execute(filters=None):
    f=frappe._dict(filters or {});business=business_filter(f);v={};c=[]
    if business:c.append("supplier.business=%(business)s");v["business"]=business
    if f.get("supplier"):c.append("supplier.name=%(supplier)s");v["supplier"]=f.supplier
    where="WHERE "+" AND ".join(c) if c else "";balance="COALESCE(SUM(CASE WHEN ledger.direction='DEBIT' THEN ledger.amount ELSE -ledger.amount END),0)";having="" if cint(f.get("show_zero_balances")) else f"HAVING {balance} != 0"
    rows=frappe.db.sql(f"""SELECT supplier.name supplier,supplier.supplier_name,supplier.phone,supplier.business,{balance} outstanding_balance,MAX(CASE WHEN ledger.transaction_type='PURCHASE' THEN ledger.transaction_date END) last_purchase_date,MAX(CASE WHEN ledger.transaction_type='SUPPLIER_PAYMENT' THEN ledger.transaction_date END) last_payment_date FROM `tabSupplier` supplier LEFT JOIN `tabSupplier Transaction` ledger ON ledger.supplier=supplier.name AND ledger.business=supplier.business AND ledger.docstatus=1 {where} GROUP BY supplier.name {having} ORDER BY outstanding_balance DESC,supplier.supplier_name""",v,as_dict=True)
    for row in rows:row.outstanding_balance=money(row.outstanding_balance)
    return [{"fieldname":"supplier","label":_("Supplier"),"fieldtype":"Link","options":"Supplier"},{"fieldname":"supplier_name","label":_("Supplier Name"),"fieldtype":"Data","width":180},{"fieldname":"phone","label":_("Phone"),"fieldtype":"Data"},money_col("outstanding_balance","Amount Owed",160),{"fieldname":"last_purchase_date","label":_("Last Purchase"),"fieldtype":"Date"},{"fieldname":"last_payment_date","label":_("Last Payment"),"fieldtype":"Date"}],rows
