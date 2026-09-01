from decimal import Decimal
import frappe
from frappe import _
from frappe.utils import cint
from creditflow.reporting import business_filter,money,money_col

def execute(filters=None):
    f=frappe._dict(filters or {});business=business_filter(f);v={};c=[]
    if business:c.append("customer.business=%(business)s");v["business"]=business
    if f.get("customer"):c.append("customer.name=%(customer)s");v["customer"]=f.customer
    where="WHERE "+" AND ".join(c) if c else ""
    balance="COALESCE(SUM(CASE WHEN ledger.direction='DEBIT' THEN ledger.amount ELSE -ledger.amount END),0)"
    having="" if cint(f.get("show_zero_balances")) else f"HAVING {balance} != 0"
    rows=frappe.db.sql(f"""SELECT customer.name customer,customer.customer_name,customer.phone,customer.business,customer.credit_limit,COALESCE(SUM(CASE WHEN ledger.direction='DEBIT' THEN ledger.amount ELSE 0 END),0) total_debit,COALESCE(SUM(CASE WHEN ledger.direction='CREDIT' THEN ledger.amount ELSE 0 END),0) total_credit,{balance} outstanding_balance FROM `tabCustomer` customer LEFT JOIN `tabCredit Transaction` ledger ON ledger.customer=customer.name AND ledger.business=customer.business AND ledger.docstatus=1 {where} GROUP BY customer.name {having} ORDER BY outstanding_balance DESC,customer.customer_name""",v,as_dict=True)
    for row in rows:
        for field in ("total_debit","total_credit","outstanding_balance","credit_limit"):row[field]=money(row[field])
        row.available_credit=money(max(Decimal("0"),row.credit_limit-row.outstanding_balance)) if row.credit_limit>0 else None
        row.credit_used_percent=(row.outstanding_balance/row.credit_limit*100) if row.credit_limit>0 else None
        row.credit_status="EXCEEDED" if row.credit_limit>0 and row.outstanding_balance>row.credit_limit else "OK"
    cols=[{"fieldname":"customer","label":_("Customer"),"fieldtype":"Link","options":"Customer"},{"fieldname":"customer_name","label":_("Customer Name"),"fieldtype":"Data","width":180},{"fieldname":"phone","label":_("Phone"),"fieldtype":"Data"},money_col("outstanding_balance","Outstanding Debt",150),money_col("credit_limit","Credit Limit"),money_col("available_credit","Available Credit"),{"fieldname":"credit_used_percent","label":_("Credit Used %"),"fieldtype":"Percent"},{"fieldname":"credit_status","label":_("Status"),"fieldtype":"Data"},money_col("total_debit","Total Debit"),money_col("total_credit","Total Credit")]
    return cols,rows
