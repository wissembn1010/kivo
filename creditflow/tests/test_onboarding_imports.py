from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow import dashboard
from creditflow.install import ensure_security_metadata, seed_default_uoms
from creditflow.onboarding import commit_csv, csv_template, preview_csv
from creditflow.creditflow.report.customer_balances.customer_balances import execute as customer_balances
from creditflow.creditflow.report.stock_on_hand.stock_on_hand import execute as stock_report


class IntegrationTestOnboardingImports(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator"); self.token=uuid4().hex[:8]
        self.business=frappe.get_doc({"doctype":"Business","business_name":f"Pilot {self.token}","address":"Tunis","phone":"71111111","email":f"pilot-{self.token}@example.com","tax_identifier":f"MF-{self.token}"}).insert()
        self.other=frappe.get_doc({"doctype":"Business","business_name":f"Other {self.token}"}).insert()
        self.owner=self.user("OWNER");self.staff=self.user("STAFF");frappe.set_user(self.owner)
    def tearDown(self):frappe.set_user("Administrator")
    def user(self,role):
        email=f"import-{role.lower()}-{uuid4().hex[:8]}@example.com";u=frappe.get_doc({"doctype":"User","email":email,"first_name":"Import User","send_welcome_email":0,"creditflow_business":self.business.name}).insert(ignore_permissions=True);u.add_roles(role);return u.name
    def commit(self,kind,text):return commit_csv(kind,text)
    def seed_masters(self):
        c=self.commit("CUSTOMERS",f"customer_name,phone,secondary_phone,address,credit_limit,price_type,notes\nPilot Customer {self.token},22111111,22222222,Tunis,1000,PROFESSIONAL,Imported\n")
        s=self.commit("SUPPLIERS",f"supplier_name,phone,address,notes\nPilot Supplier {self.token},71111111,Sfax,Imported\n")
        p=self.commit("PRODUCTS",f"product_name,reference,selling_price,professional_price,minimum_selling_price,tva_rate,base_uom,minimum_stock,active\nPilot Product {self.token},SKU-{self.token},100,90,80,19,UNIT,2,1\n")
        return c["created"][0],s["created"][0],p["created"][0]
    def test_customer_supplier_and_product_imports(self):
        customer,supplier,product=self.seed_masters()
        self.assertEqual(frappe.db.get_value("Customer",customer,["business","price_type"]),(self.business.name,"PROFESSIONAL"))
        self.assertEqual(frappe.db.get_value("Supplier",supplier,"notes"),"Imported")
        self.assertEqual(frappe.db.get_value("Product",product,["business","tva_rate","primary_unit"]),(self.business.name,19.0,"UNIT"))
        self.assertIn("customer_name",csv_template("CUSTOMERS"))
    def test_malformed_and_invalid_product_rows_reject_commit(self):
        text="customer_name,credit_limit\nGood,10\nBad,-1\n";preview=preview_csv("CUSTOMERS",text);self.assertEqual((preview["valid_count"],preview["invalid_count"]),(1,1));before=frappe.db.count("Customer",{"business":self.business.name})
        with self.assertRaisesRegex(frappe.ValidationError,"row 3"):self.commit("CUSTOMERS",text)
        self.assertEqual(frappe.db.count("Customer",{"business":self.business.name}),before)
        invalid=f"product_name,reference,tva_rate,base_uom\nBad,INVALID-{self.token},101,UNIT\n"
        with self.assertRaisesRegex(frappe.ValidationError,"tva_rate"):self.commit("PRODUCTS",invalid)
    def test_opening_import_preview_commit_and_duplicate_protection(self):
        customer,supplier,product=self.seed_masters();stock=f"product,quantity,date,note,reference\nSKU-{self.token},12,,Initial,STK-{self.token}\n";debt=f"customer,amount,date,note,reference\nPilot Customer {self.token},50,,Legacy,CUST-{self.token}\n";supplier_debt=f"supplier,amount,date,note,reference\nPilot Supplier {self.token},70,,Legacy,SUP-{self.token}\n"
        before=frappe.db.count("Stock Movement",{"product":product});preview=preview_csv("OPENING_STOCK",stock);self.assertEqual(preview["total"],Decimal("12"));self.assertEqual(frappe.db.count("Stock Movement",{"product":product}),before)
        self.commit("OPENING_STOCK",stock);self.commit("OPENING_CUSTOMER_DEBT",debt);self.commit("OPENING_SUPPLIER_DEBT",supplier_debt)
        self.assertEqual(frappe.db.get_value("Stock Movement",{"product":product,"movement_reason":"OPENING_STOCK"},"docstatus"),1)
        self.assertEqual(Decimal(str(frappe.get_doc("Customer",customer).get_ledger_balance())),Decimal("50.000"));self.assertEqual(Decimal(str(frappe.get_doc("Supplier",supplier).get_ledger_balance())),Decimal("70.000"))
        with self.assertRaisesRegex(frappe.ValidationError,"already imported"):self.commit("OPENING_STOCK",stock)
    def test_staff_and_cross_business_attacks_are_denied(self):
        text=f"customer_name\nAttack {self.token}\n";frappe.set_user(self.staff)
        with self.assertRaises(frappe.PermissionError):preview_csv("CUSTOMERS",text)
        frappe.set_user(self.owner)
        with self.assertRaises(frappe.PermissionError):preview_csv("CUSTOMERS",text,self.other.name)
        attack="product,quantity\nDOES-NOT-BELONG,1\n"
        result=preview_csv("OPENING_STOCK",attack);self.assertEqual(result["invalid_count"],1)
    def test_clean_install_metadata_is_complete_and_idempotent(self):
        frappe.set_user("Administrator");ensure_security_metadata();seed_default_uoms();ensure_security_metadata();seed_default_uoms()
        for role in ("OWNER","STAFF"):self.assertTrue(frappe.db.exists("Role",role))
        self.assertTrue(frappe.db.exists("Custom Field","User-creditflow_business"));self.assertTrue(frappe.db.exists("Workspace","Kivo"));self.assertTrue(frappe.db.exists("Page","creditflow-import"))
        for report in ("Sales Report","Purchases Report","Customer Statement","Supplier Statement","Product Performance","Customer Balances","Stock On Hand"):self.assertTrue(frappe.db.exists("Report",report),report)
        for card in ("Net Sales Today","Net Sales This Month","Cash Collected Today","Total Customer Debt","Total Supplier Debt","Purchases This Month","Returns This Month","Low / Out of Stock Products"):self.assertTrue(frappe.db.exists("Number Card",card),card)
        for fmt in ("Fiscal Invoice","Payment Receipt","Sale Return Receipt"):self.assertTrue(frappe.db.exists("Print Format",fmt),fmt)
        for uom in ("UNIT","KG","BOX"):self.assertTrue(frappe.db.exists("CreditFlow UOM",uom))
    def test_first_business_end_to_end_after_imports(self):
        customer,supplier,product=self.seed_masters();self.commit("OPENING_STOCK",f"product,quantity\nSKU-{self.token},10\n");self.commit("OPENING_CUSTOMER_DEBT",f"customer,amount\nPilot Customer {self.token},20\n");self.commit("OPENING_SUPPLIER_DEBT",f"supplier,amount\nPilot Supplier {self.token},30\n")
        purchase=frappe.get_doc({"doctype":"Purchase","business":self.business.name,"supplier":supplier,"business_date":frappe.utils.today(),"items":[{"product":product,"quantity":2,"unit_cost":40}]}).insert();purchase.submit()
        sale=frappe.get_doc({"doctype":"Sale","business":self.business.name,"customer":customer,"business_date":frappe.utils.today(),"sale_mode":"CREDIT","amount_paid":0,"items":[{"product":product,"quantity":2,"unit_price":100}]}).insert();sale.submit();self.assertEqual(Decimal(str(sale.total_ttc)),Decimal("238.000"))
        payment=frappe.get_doc({"doctype":"Payment","business":self.business.name,"customer":customer,"business_date":frappe.utils.today(),"amount":50,"payment_method":"CASH"}).insert();payment.submit()
        supplier_payment=frappe.get_doc({"doctype":"Supplier Payment","business":self.business.name,"supplier":supplier,"business_date":frappe.utils.today(),"amount":10,"payment_method":"CASH"}).insert();supplier_payment.submit()
        ret=frappe.get_doc({"doctype":"Sale Return","business":self.business.name,"original_sale":sale.name,"return_date":frappe.utils.today(),"reason":"Pilot","items":[{"original_sale_item":sale.items[0].name,"quantity":0.5}]}).insert();ret.submit()
        _,balances=customer_balances({});row=next(r for r in balances if r.customer==customer);self.assertEqual(row.outstanding_balance,Decimal("148.500"))
        _,stocks=stock_report({"show_zero_stock":1});stock=next(r for r in stocks if r.product==product);self.assertEqual(Decimal(str(stock.stock_on_hand)),Decimal("10.5"));self.assertGreater(dashboard.net_sales_this_month()["value"],0)
