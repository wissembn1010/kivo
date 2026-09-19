from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import frappe
from creditflow.permissions import get_user_business,has_cross_business_access
MONEY=Decimal("0.001")

def _scope(alias):
    if has_cross_business_access():return "",{}
    business=get_user_business()
    return (f" AND {alias}.business=%(business)s",{"business":business}) if business else (" AND 1=0",{})
def _card(value,route):return {"value":Decimal(str(value or 0)).quantize(MONEY,rounding=ROUND_HALF_UP),"fieldtype":"Currency","precision":3,"route":route}
def _range(kind):
    today=frappe.utils.getdate(frappe.utils.today())
    if kind=="today":return today,today
    return today.replace(day=1),today

def _net_sales(kind):
    start,end=_range(kind);scope,v=_scope("activity");v.update(start=start,end=end)
    result=frappe.db.sql(f"""SELECT COALESCE(SUM(amount),0) FROM (
      SELECT sale.business,COALESCE(NULLIF(sale.total_ttc,0),sale.total_amount) amount FROM `tabSale` sale WHERE sale.docstatus=1 AND sale.business_date BETWEEN %(start)s AND %(end)s
      UNION ALL SELECT ret.business,-ret.return_ttc FROM `tabSale Return` ret WHERE ret.docstatus=1 AND ret.return_date BETWEEN %(start)s AND %(end)s
      UNION ALL SELECT rev.business,-COALESCE(NULLIF(sale.total_ttc,0),sale.total_amount) FROM `tabSale Reversal` rev INNER JOIN `tabSale` sale ON sale.name=rev.original_sale WHERE rev.docstatus=1 AND rev.business_date BETWEEN %(start)s AND %(end)s
    ) activity WHERE 1=1 {scope}""",v)[0][0]
    return _card(result,["query-report","Sales Report"])
@frappe.whitelist()
def net_sales_today(filters=None):return _net_sales("today")
@frappe.whitelist()
def sales_today(filters=None):return net_sales_today(filters)
@frappe.whitelist()
def net_sales_this_month(filters=None):return _net_sales("month")

def _ledger_total(table):
    scope,v=_scope("ledger");result=frappe.db.sql(f"SELECT COALESCE(SUM(CASE WHEN direction='DEBIT' THEN amount ELSE -amount END),0) FROM `tab{table}` ledger WHERE docstatus=1 {scope}",v)[0][0];return result
@frappe.whitelist()
def total_customer_debt(filters=None):return _card(_ledger_total("Credit Transaction"),["query-report","Customer Balances"])
@frappe.whitelist()
def total_supplier_debt(filters=None):return _card(_ledger_total("Supplier Transaction"),["query-report","Supplier Balances"])

def _document_net(doctype,reversal,original_field,kind,amount="total_amount"):
    start,end=_range(kind);scope,v=_scope("activity");v.update(start=start,end=end)
    val=frappe.db.sql(f"""SELECT COALESCE(SUM(amount),0) FROM (SELECT d.business,d.{amount} amount FROM `tab{doctype}` d WHERE d.docstatus=1 AND d.business_date BETWEEN %(start)s AND %(end)s UNION ALL SELECT r.business,-d.{amount} FROM `tab{reversal}` r INNER JOIN `tab{doctype}` d ON d.name=r.{original_field} WHERE r.docstatus=1 AND r.business_date BETWEEN %(start)s AND %(end)s) activity WHERE 1=1 {scope}""",v)[0][0]
    return val
@frappe.whitelist()
def purchases_this_month(filters=None):return _card(_document_net("Purchase","Purchase Reversal","original_purchase","month"),["query-report","Purchases Report"])
@frappe.whitelist()
def purchases_today(filters=None):return _card(_document_net("Purchase","Purchase Reversal","original_purchase","today"),["query-report","Purchases Report"])
@frappe.whitelist()
def payments_today(filters=None):return _card(_document_net("Payment","Payment Reversal","original_payment","today","amount"),["List","Payment"])
@frappe.whitelist()
def supplier_payments_today(filters=None):
    # A reversal corrects the supplier ledger; it is not evidence of cash returned.
    start,end=_range("today");scope,v=_scope("payment");v.update(start=start,end=end)
    value=frappe.db.sql(f"SELECT COALESCE(SUM(CASE WHEN payment.withholding_enabled=1 THEN payment.amount-COALESCE(payment.withholding_amount,0) ELSE payment.amount END),0) FROM `tabSupplier Payment` payment WHERE payment.docstatus=1 AND payment.business_date BETWEEN %(start)s AND %(end)s {scope}",v)[0][0]
    return _card(value,["List","Supplier Payment"])
@frappe.whitelist()
def returns_this_month(filters=None):
    start,end=_range("month");scope,v=_scope("ret");v.update(start=start,end=end);val=frappe.db.sql(f"SELECT COALESCE(SUM(ret.return_ttc),0) FROM `tabSale Return` ret WHERE ret.docstatus=1 AND ret.return_date BETWEEN %(start)s AND %(end)s {scope}",v)[0][0];return _card(val,["List","Sale Return"])
@frappe.whitelist()
def cash_collected_today(filters=None):
    today=frappe.utils.today();scope,v=_scope("activity");v["today"]=today
    val=frappe.db.sql(f"""SELECT COALESCE(SUM(amount),0) FROM (
      SELECT sale.business,sale.amount_paid amount FROM `tabSale` sale WHERE sale.docstatus=1 AND sale.business_date=%(today)s AND sale.payment_method='CASH'
      UNION ALL SELECT payment.business,payment.amount FROM `tabPayment` payment WHERE payment.docstatus=1 AND payment.business_date=%(today)s AND payment.payment_method='CASH'
      UNION ALL SELECT reversal.business,-payment.amount FROM `tabPayment Reversal` reversal INNER JOIN `tabPayment` payment ON payment.name=reversal.original_payment WHERE reversal.docstatus=1 AND reversal.business_date=%(today)s AND payment.payment_method='CASH'
      UNION ALL SELECT ret.business,-ret.refund_amount FROM `tabSale Return` ret WHERE ret.docstatus=1 AND ret.refund_status='REFUNDED' AND ret.refund_method='CASH' AND ret.refund_date=%(today)s
    ) activity WHERE 1=1 {scope}""",v)[0][0]
    return _card(val,["List","Payment"])
@frappe.whitelist()
def low_stock_products(filters=None):
    scope,v=_scope("product");count=frappe.db.sql(f"""SELECT COUNT(*) FROM (SELECT product.business,product.name,product.minimum_stock,COALESCE(SUM(CASE WHEN movement.direction='IN' THEN movement.quantity ELSE -movement.quantity END),0) stock FROM `tabProduct` product LEFT JOIN `tabStock Movement` movement ON movement.product=product.name AND movement.business=product.business AND movement.docstatus=1 WHERE 1=1 {scope} GROUP BY product.name HAVING stock<=0 OR (product.minimum_stock>0 AND stock<=product.minimum_stock)) x""",v)[0][0];return {"value":int(count or 0),"fieldtype":"Int","route":["query-report","Stock On Hand"],"route_options":{"show_zero_stock":1}}
@frappe.whitelist()
def negative_stock_products(filters=None):
    scope, values = _scope("product")
    count = frappe.db.sql(f"""
        SELECT COUNT(*) FROM (
            SELECT product.name,
                COALESCE(SUM(CASE WHEN movement.direction='IN' THEN movement.quantity
                    ELSE -movement.quantity END), 0) stock
            FROM `tabProduct` product
            LEFT JOIN `tabStock Movement` movement ON movement.product=product.name
                AND movement.business=product.business AND movement.docstatus=1
            WHERE 1=1 {scope}
            GROUP BY product.name HAVING stock < 0
        ) negative_products
    """, values)[0][0]
    return {"value": int(count or 0), "fieldtype": "Int",
            "route": ["query-report", "Stock On Hand"],
            "route_options": {"show_negative_stock_only": 1}}
