import frappe
from frappe import _
from creditflow.reporting import business_filter,money,money_col

def execute(filters=None):
    f=frappe._dict(filters or {});business=business_filter(f)
    if not f.get("supplier"):frappe.throw(_("Supplier is required."))
    if business and frappe.db.get_value("Supplier",f.supplier,"business")!=business:frappe.throw(_("Supplier is outside your Business."),frappe.PermissionError)
    v={"supplier":f.supplier};date=[]
    if f.get("from_date"):date.append("transaction_date >= %(from_date)s");v["from_date"]=f.from_date
    if f.get("to_date"):date.append("transaction_date <= %(to_date)s");v["to_date"]=f.to_date
    opening=frappe.db.sql(f"SELECT COALESCE(SUM(CASE WHEN direction='DEBIT' THEN amount ELSE -amount END),0) FROM `tabSupplier Transaction` WHERE supplier=%(supplier)s AND docstatus=1 {'AND transaction_date < %(from_date)s' if f.get('from_date') else 'AND 1=0'}",v)[0][0]
    rows=frappe.db.sql(f"""SELECT transaction_date,transaction_type,direction,amount,COALESCE(source_purchase,source_supplier_payment,source_purchase_reversal,source_supplier_payment_reversal,name) reference,notes FROM `tabSupplier Transaction` WHERE supplier=%(supplier)s AND docstatus=1 {'AND '+' AND '.join(date) if date else ''} ORDER BY transaction_date,creation,name""",v,as_dict=True)
    balance=money(opening);data=[]
    if f.get("from_date"):data.append(frappe._dict(transaction_date=f.from_date,transaction_type="OPENING",reference="Opening balance",debit=money(opening) if balance>=0 else money(0),credit=money(-opening) if balance<0 else money(0),running_balance=balance))
    for row in rows:row.debit=money(row.amount) if row.direction=="DEBIT" else money(0);row.credit=money(row.amount) if row.direction=="CREDIT" else money(0);balance=money(balance+row.debit-row.credit);row.running_balance=balance;data.append(row)
    if data:
        data.append(frappe._dict(transaction_type=_("Total"),debit=money(sum(row.debit for row in data)),credit=money(sum(row.credit for row in data)),running_balance=balance))
    columns=[{"fieldname":"transaction_date","label":_("Date"),"fieldtype":"Date"},{"fieldname":"transaction_type","label":_("Movement"),"fieldtype":"Data"},{"fieldname":"reference","label":_("Reference"),"fieldtype":"Data","width":180},money_col("debit","Debit"),money_col("credit","Credit"),money_col("running_balance","Running Balance",150)]
    return columns,data,None,None,None,True
