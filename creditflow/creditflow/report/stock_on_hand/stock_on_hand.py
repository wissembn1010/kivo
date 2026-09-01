from decimal import Decimal
import frappe
from frappe import _
from frappe.utils import cint
from creditflow.reporting import business_filter,money_col

def execute(filters=None):
    f=frappe._dict(filters or {});business=business_filter(f);v={};c=[]
    if business:c.append("product.business=%(business)s");v["business"]=business
    if f.get("product"):c.append("product.name=%(product)s");v["product"]=f.product
    where="WHERE "+" AND ".join(c) if c else "";stock="COALESCE(SUM(CASE WHEN movement.direction='IN' THEN movement.quantity ELSE -movement.quantity END),0)"
    if cint(f.get("show_negative_stock_only")):having=f"HAVING {stock}<0"
    elif not cint(f.get("show_zero_stock")):having=f"HAVING {stock}!=0"
    else:having=""
    rows=frappe.db.sql(f"""SELECT product.name product,product.reference,product.product_name,product.primary_unit unit,product.business,product.selling_price,product.minimum_selling_price,product.minimum_stock,{stock} stock_on_hand FROM `tabProduct` product LEFT JOIN `tabStock Movement` movement ON movement.product=product.name AND movement.business=product.business AND movement.docstatus=1 {where} GROUP BY product.name {having} ORDER BY stock_on_hand ASC,product.product_name""",v,as_dict=True)
    for row in rows:
        row.stock_on_hand=Decimal(str(row.stock_on_hand or 0));threshold=Decimal(str(row.minimum_stock or 0))
        row.stock_status="Negative" if row.stock_on_hand<0 else "Out of Stock" if row.stock_on_hand==0 else "Low Stock" if threshold>0 and row.stock_on_hand<=threshold else "In Stock"
    return [{"fieldname":"product","label":_("Product"),"fieldtype":"Link","options":"Product"},{"fieldname":"reference","label":_("Reference / SKU"),"fieldtype":"Data"},{"fieldname":"product_name","label":_("Product Name"),"fieldtype":"Data","width":180},{"fieldname":"unit","label":_("Base UOM"),"fieldtype":"Data"},{"fieldname":"stock_on_hand","label":_("Current Stock"),"fieldtype":"Float"},money_col("selling_price","Selling Price"),money_col("minimum_selling_price","Minimum Price"),{"fieldname":"minimum_stock","label":_("Minimum Stock"),"fieldtype":"Float"},{"fieldname":"stock_status","label":_("Stock Status"),"fieldtype":"Data"}],rows
