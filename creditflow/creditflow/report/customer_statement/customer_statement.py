import frappe
from frappe import _
from creditflow.reporting import business_filter,money,money_col

def execute(filters=None):
    f=frappe._dict(filters or {}); business=business_filter(f)
    if not f.get("customer"):frappe.throw(_("Customer is required."))
    customer_business=frappe.db.get_value("Customer",f.customer,"business")
    if business and customer_business!=business:frappe.throw(_("Customer is outside your Business."),frappe.PermissionError)
    values={"customer":f.customer}; date=[]
    if f.get("from_date"):date.append("transaction_date >= %(from_date)s");values["from_date"]=f.from_date
    if f.get("to_date"):date.append("transaction_date <= %(to_date)s");values["to_date"]=f.to_date
    opening=frappe.db.sql(f"SELECT COALESCE(SUM(CASE WHEN direction='DEBIT' THEN amount ELSE -amount END),0) FROM `tabCredit Transaction` WHERE customer=%(customer)s AND docstatus=1 {'AND transaction_date < %(from_date)s' if f.get('from_date') else 'AND 1=0'}",values)[0][0]
    rows=frappe.db.sql(f"""SELECT transaction_date,transaction_type,direction,amount,COALESCE(source_sale,source_payment,source_sale_reversal,source_sale_return,source_payment_reversal,name) reference,notes FROM `tabCredit Transaction` WHERE customer=%(customer)s AND docstatus=1 {'AND '+' AND '.join(date) if date else ''} ORDER BY transaction_date,creation,name""",values,as_dict=True)
    balance=money(opening); data=[]
    if f.get("from_date"):data.append(frappe._dict(transaction_date=f.from_date,transaction_type="OPENING",direction="",debit=money(opening) if balance>=0 else money(0),credit=money(-opening) if balance<0 else money(0),running_balance=balance,reference="Opening balance"))
    for row in rows:
        debit=money(row.amount) if row.direction=="DEBIT" else money(0);credit=money(row.amount) if row.direction=="CREDIT" else money(0);balance=money(balance+debit-credit);row.debit=debit;row.credit=credit;row.running_balance=balance;data.append(row)
    cols=[{"fieldname":"transaction_date","label":_("Date"),"fieldtype":"Date"},{"fieldname":"transaction_type","label":_("Movement"),"fieldtype":"Data"},{"fieldname":"reference","label":_("Reference"),"fieldtype":"Data","width":180},money_col("debit","Debit"),money_col("credit","Credit"),money_col("running_balance","Running Balance",150)]
    return cols,data
