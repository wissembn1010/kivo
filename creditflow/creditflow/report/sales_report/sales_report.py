import frappe
from frappe import _
from creditflow.reporting import business_filter, date_conditions, money, money_col, apply_period


def execute(filters=None):
    filters=apply_period(frappe._dict(filters or {})); business=business_filter(filters); values={}; conditions=["sale.docstatus=1"]
    if business: conditions.append("sale.business=%(business)s"); values["business"]=business
    conditions += date_conditions(filters,"sale.business_date",values)
    if filters.get("customer"): conditions.append("sale.customer=%(customer)s"); values["customer"]=filters.customer
    rows=frappe.db.sql(f"""SELECT sale.business_date sale_date,sale.invoice_number,sale.name sale,sale.customer,COALESCE(customer.customer_name,'Walk-in Customer') customer_name,sale.sale_mode,sale.total_ht,sale.total_tva,sale.total_ttc,sale.amount_paid,sale.outstanding_amount,
      CASE WHEN EXISTS(SELECT 1 FROM `tabSale Reversal` rev WHERE rev.original_sale=sale.name AND rev.docstatus=1) THEN sale.total_ttc ELSE COALESCE((SELECT SUM(ret.return_ttc) FROM `tabSale Return` ret WHERE ret.original_sale=sale.name AND ret.docstatus=1),0) END returned_amount,
      CASE WHEN EXISTS(SELECT 1 FROM `tabSale Reversal` rev WHERE rev.original_sale=sale.name AND rev.docstatus=1) THEN 0 ELSE sale.total_ttc-COALESCE((SELECT SUM(ret.return_ttc) FROM `tabSale Return` ret WHERE ret.original_sale=sale.name AND ret.docstatus=1),0) END net_sale_value
      FROM `tabSale` sale LEFT JOIN `tabCustomer` customer ON customer.name=sale.customer WHERE {' AND '.join(conditions)} ORDER BY sale.business_date,sale.creation""",values,as_dict=True)
    for row in rows:
        for field in ("total_ht","total_tva","total_ttc","amount_paid","outstanding_amount","returned_amount","net_sale_value"): row[field]=money(row[field])
    columns=[{"fieldname":"sale_date","label":_("Sale Date"),"fieldtype":"Date","width":100},{"fieldname":"invoice_number","label":_("Invoice Number"),"fieldtype":"Data","width":140},{"fieldname":"customer_name","label":_("Customer"),"fieldtype":"Data","width":180},{"fieldname":"sale_mode","label":_("Sale Mode"),"fieldtype":"Data","width":90}]+[money_col(f,l) for f,l in (("total_ht","Total HT"),("total_tva","Total TVA"),("total_ttc","Total TTC"),("amount_paid","Paid Immediately"),("outstanding_amount","Debt Created"),("returned_amount","Returned"),("net_sale_value","Net Sale"))]
    return columns,rows
