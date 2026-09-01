from decimal import Decimal
from uuid import uuid4
import frappe
from frappe.tests import IntegrationTestCase
from creditflow import dashboard
from creditflow.creditflow.report.sales_report.sales_report import execute as sales_report
from creditflow.creditflow.report.purchases_report.purchases_report import execute as purchases_report
from creditflow.creditflow.report.customer_statement.customer_statement import execute as customer_statement
from creditflow.creditflow.report.supplier_statement.supplier_statement import execute as supplier_statement
from creditflow.creditflow.report.customer_balances.customer_balances import execute as customer_balances
from creditflow.creditflow.report.stock_on_hand.stock_on_hand import execute as stock_report
from creditflow.creditflow.report.product_performance.product_performance import execute as product_performance

class IntegrationTestSMEReporting(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator");x=uuid4().hex[:8]
        self.business=frappe.get_doc({"doctype":"Business","business_name":f"Report {x}"}).insert();self.other=frappe.get_doc({"doctype":"Business","business_name":f"Other {x}"}).insert()
        self.customer=frappe.get_doc({"doctype":"Customer","business":self.business.name,"customer_name":f"Customer {x}","phone":"123","credit_limit":2000}).insert()
        self.supplier=frappe.get_doc({"doctype":"Supplier","business":self.business.name,"supplier_name":f"Supplier {x}"}).insert()
        self.product=frappe.get_doc({"doctype":"Product","business":self.business.name,"product_name":f"Product {x}","reference":f"REP-{x}","tva_rate":19,"minimum_stock":5}).insert()
        m=frappe.get_doc({"doctype":"Stock Movement","business":self.business.name,"product":self.product.name,"direction":"IN","quantity":100,"movement_reason":"OPENING_STOCK","business_date":frappe.utils.today()}).insert();m.submit()
    def sale(self,mode="CREDIT",paid=0,qty=1):
        s=frappe.get_doc({"doctype":"Sale","business":self.business.name,"customer":self.customer.name,"business_date":frappe.utils.today(),"sale_mode":mode,"amount_paid":paid,"payment_method":"CASH" if paid else None,"items":[{"product":self.product.name,"quantity":qty,"unit_price":100}]}).insert();s.submit();return s
    def purchase(self):
        p=frappe.get_doc({"doctype":"Purchase","business":self.business.name,"supplier":self.supplier.name,"business_date":frappe.utils.today(),"items":[{"product":self.product.name,"quantity":1,"unit_cost":40}]}).insert();p.submit();return p
    def ret(self,sale,qty):
        r=frappe.get_doc({"doctype":"Sale Return","business":self.business.name,"original_sale":sale.name,"return_date":frappe.utils.today(),"reason":"Report","items":[{"original_sale_item":sale.items[0].name,"quantity":qty}]}).insert();r.submit();return r
    def test_sales_modes_dates_returns_and_tenant(self):
        cash=self.sale("CASH",119);credit=self.sale();mixed=self.sale("MIXED",50);self.ret(credit,.25);self.ret(credit,.25)
        _,rows=sales_report({"business":self.business.name,"from_date":frappe.utils.today(),"to_date":frappe.utils.today()});by={r.sale:r for r in rows}
        self.assertEqual({by[cash.name].sale_mode,by[credit.name].sale_mode,by[mixed.name].sale_mode},{"CASH","CREDIT","MIXED"})
        self.assertEqual(by[credit.name].returned_amount,Decimal("59.500"));self.assertEqual(by[credit.name].net_sale_value,Decimal("59.500"))
        _,none=sales_report({"business":self.business.name,"from_date":"2000-01-01","to_date":"2000-01-02"});self.assertFalse(none)
        user=frappe.get_doc({"doctype":"User","email":f"report-{uuid4().hex[:8]}@example.com","first_name":"Report","send_welcome_email":0,"creditflow_business":self.business.name}).insert(ignore_permissions=True);user.add_roles("STAFF");frappe.set_user(user.name)
        with self.assertRaises(frappe.PermissionError):sales_report({"business":self.other.name})
    def test_full_reversal_removes_net_sales(self):
        sale=self.sale();rev=frappe.get_doc({"doctype":"Sale Reversal","business":self.business.name,"original_sale":sale.name,"business_date":frappe.utils.today(),"reason":"Report"}).insert();rev.submit();_,rows=sales_report({"business":self.business.name});row=next(x for x in rows if x.sale==sale.name);self.assertEqual(row.net_sale_value,Decimal("0.000"))
    def test_purchase_and_reversal_report(self):
        p=self.purchase();_,rows=purchases_report({"business":self.business.name});self.assertEqual(next(x for x in rows if x.purchase==p.name).reversal_status,"Active")
        r=frappe.get_doc({"doctype":"Purchase Reversal","business":self.business.name,"original_purchase":p.name,"business_date":frappe.utils.today(),"reason":"Report"}).insert();r.submit();_,rows=purchases_report({"business":self.business.name});row=next(x for x in rows if x.purchase==p.name);self.assertEqual(row.supplier_debt_effect,Decimal("0.000"))
    def test_statements_reconcile_running_balances(self):
        sale=self.sale();pay=frappe.get_doc({"doctype":"Payment","business":self.business.name,"customer":self.customer.name,"business_date":frappe.utils.today(),"amount":19,"payment_method":"CASH"}).insert();pay.submit();self.ret(sale,.25)
        _,rows=customer_statement({"customer":self.customer.name});self.assertEqual(rows[-1].running_balance,Decimal(str(frappe.get_doc("Customer",self.customer.name).get_ledger_balance())))
        self.purchase();sp=frappe.get_doc({"doctype":"Supplier Payment","business":self.business.name,"supplier":self.supplier.name,"business_date":frappe.utils.today(),"amount":10,"payment_method":"CASH"}).insert();sp.submit();_,rows,*_=supplier_statement({"supplier":self.supplier.name});self.assertEqual(rows[-1].running_balance,Decimal(str(frappe.get_doc("Supplier",self.supplier.name).get_ledger_balance())))
    def test_balances_stock_and_product_performance(self):
        sale=self.sale(qty=2);self.ret(sale,.5);_,balances=customer_balances({"business":self.business.name});row=next(x for x in balances if x.customer==self.customer.name);self.assertEqual(row.credit_limit,Decimal("2000.000"));self.assertEqual(row.credit_status,"OK")
        _,stock=stock_report({"business":self.business.name,"show_zero_stock":1});s=next(x for x in stock if x.product==self.product.name);self.assertEqual(Decimal(str(s.stock_on_hand)),Decimal("98.5"))
        _,perf=product_performance({"business":self.business.name});p=next(x for x in perf if x.product==self.product.name);self.assertEqual(Decimal(str(p.quantity_returned)),Decimal("0.5"));self.assertEqual(Decimal(str(p.net_quantity_sold)),Decimal("1.5"))
    def test_customer_statement_has_no_one_thousand_row_limit(self):
        now = frappe.utils.now()
        fields = ["name", "owner", "creation", "modified", "modified_by", "docstatus", "business", "customer", "transaction_type", "direction", "amount", "transaction_date"]
        values = [(f"BULK-{uuid4().hex}", "Administrator", now, now, "Administrator", 1, self.business.name, self.customer.name, "OPENING_BALANCE", "DEBIT", 1, frappe.utils.today()) for _ in range(1001)]
        frappe.db.bulk_insert("Credit Transaction", fields=fields, values=values)
        _, rows = customer_statement({"customer": self.customer.name})
        self.assertEqual(len(rows), 1001)
        self.assertEqual(rows[-1].running_balance, Decimal("1001.000"))

    def test_payment_reversal_and_dashboard_net_semantics(self):
        sale=self.sale();payment=frappe.get_doc({"doctype":"Payment","business":self.business.name,"customer":self.customer.name,"business_date":frappe.utils.today(),"amount":19,"payment_method":"CASH"}).insert();payment.submit();before=dashboard.payments_today()["value"]
        reversal=frappe.get_doc({"doctype":"Payment Reversal","business":self.business.name,"original_payment":payment.name,"business_date":frappe.utils.today(),"reason":"Report"}).insert();reversal.submit();self.assertEqual(dashboard.payments_today()["value"],before-Decimal("19.000"));self.ret(sale,.5);self.assertGreaterEqual(dashboard.returns_this_month()["value"],Decimal("59.500"))
