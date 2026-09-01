import frappe
from decimal import Decimal
from frappe import _
from creditflow.reporting import business_filter,date_conditions,money,money_col, apply_period

def execute(filters=None):
    f=apply_period(frappe._dict(filters or {}));business=business_filter(f);v={};c=["sale.docstatus=1","rev.name IS NULL"]
    if business:c.append("sale.business=%(business)s");v["business"]=business
    c+=date_conditions(f,"sale.business_date",v)
    if f.get("product"):c.append("item.product=%(product)s");v["product"]=f.product
    rows=frappe.db.sql(f"""SELECT item.product,MAX(item.product_name) product_name,SUM(item.base_quantity) quantity_sold,SUM(item.line_ttc) gross_sales_value,COALESCE(SUM(ret.return_base_quantity),0) quantity_returned,COALESCE(SUM(ret.return_ttc),0) returned_value FROM `tabSale Item` item INNER JOIN `tabSale` sale ON sale.name=item.parent LEFT JOIN `tabSale Reversal` rev ON rev.original_sale=sale.name AND rev.docstatus=1 LEFT JOIN (SELECT ri.original_sale_item,SUM(ri.base_quantity) return_base_quantity,SUM(ri.return_ttc) return_ttc FROM `tabSale Return Item` ri INNER JOIN `tabSale Return` r ON r.name=ri.parent AND r.docstatus=1 GROUP BY ri.original_sale_item) ret ON ret.original_sale_item=item.name WHERE {' AND '.join(c)} GROUP BY item.product ORDER BY gross_sales_value DESC""",v,as_dict=True)
    for row in rows:row.quantity_sold=Decimal(str(row.quantity_sold or 0));row.quantity_returned=Decimal(str(row.quantity_returned or 0));row.net_quantity_sold=row.quantity_sold-row.quantity_returned;row.gross_sales_value=money(row.gross_sales_value);row.returned_value=money(row.returned_value);row.net_sales_value=money(row.gross_sales_value-row.returned_value)
    return [{"fieldname":"product","label":_("Product"),"fieldtype":"Link","options":"Product"},{"fieldname":"product_name","label":_("Product Name"),"fieldtype":"Data","width":180},{"fieldname":"quantity_sold","label":_("Quantity Sold (Base UOM)"),"fieldtype":"Float"},money_col("gross_sales_value","Gross Sales TTC"),{"fieldname":"quantity_returned","label":_("Quantity Returned"),"fieldtype":"Float"},{"fieldname":"net_quantity_sold","label":_("Net Quantity Sold"),"fieldtype":"Float"},money_col("net_sales_value","Net Sales TTC")],rows
