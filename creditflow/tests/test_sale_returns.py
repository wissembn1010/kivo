from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestSaleReturns(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        token = uuid4().hex[:8]
        self.business = frappe.get_doc({"doctype":"Business","business_name":f"Return Business {token}"}).insert()
        self.other_business = frappe.get_doc({"doctype":"Business","business_name":f"Other Return Business {token}"}).insert()
        self.customer = frappe.get_doc({"doctype":"Customer","business":self.business.name,"customer_name":f"Return Customer {token}"}).insert()
        self.product = frappe.get_doc({"doctype":"Product","business":self.business.name,"product_name":f"Return Product {token}","reference":f"RET-{token}","tva_rate":19,"primary_unit":"UNIT","uom_conversions":[{"uom":"BOX","conversion_factor":12}]}).insert()
        stock = frappe.get_doc({"doctype":"Stock Movement","business":self.business.name,"product":self.product.name,"direction":"IN","quantity":1000,"movement_reason":"OPENING_STOCK","business_date":frappe.utils.today()}).insert(); stock.submit()

    def tearDown(self): frappe.set_user("Administrator")

    def make_user(self, role):
        email = f"return-{role.lower()}-{uuid4().hex[:8]}@example.com"
        user = frappe.get_doc({"doctype":"User","email":email,"first_name":"Return User","send_welcome_email":0,"creditflow_business":self.business.name}).insert(ignore_permissions=True); user.add_roles(role); return user.name

    def sale(self, mode="CREDIT", paid=0, qty=10, uom="UNIT"):
        doc = frappe.get_doc({"doctype":"Sale","business":self.business.name,"customer":self.customer.name,"business_date":frappe.utils.today(),"sale_mode":mode,"amount_paid":paid,"payment_method":"CASH" if paid else None,"items":[{"product":self.product.name,"quantity":qty,"uom":uom,"unit_price":100,"discount_percent":0}]}).insert(); doc.submit(); return doc

    def returned(self, sale, qty, submit=True):
        doc = frappe.get_doc({"doctype":"Sale Return","business":self.business.name,"original_sale":sale.name,"return_date":frappe.utils.today(),"reason":"Customer return","notes":"Test note","items":[{"original_sale_item":sale.items[0].name,"quantity":qty}]})
        doc.insert()
        if submit: doc.submit()
        return doc

    def balance(self): return Decimal(str(frappe.get_doc("Customer", self.customer.name).get_ledger_balance()))

    def test_credit_partial_and_full_return_restore_stock_and_debt(self):
        sale = self.sale(qty=2)
        first = self.returned(sale, 0.5)
        self.assertEqual((Decimal(str(first.return_ht)), Decimal(str(first.return_tva)), Decimal(str(first.return_ttc))), (Decimal("50.000"), Decimal("9.500"), Decimal("59.500")))
        self.assertEqual((Decimal(str(first.debt_reduction)), Decimal(str(first.refund_amount))), (Decimal("59.500"), Decimal("0.000")))
        self.assertEqual(self.balance(), Decimal("178.500"))
        second = self.returned(sale, 1.5)
        self.assertEqual(self.balance(), Decimal("0.000"))
        self.assertEqual(frappe.db.get_value("Stock Movement", {"source_sale_return_item":first.items[0].name}, "quantity"), 0.5)
        self.assertEqual(second.refund_status, "NO_REFUND")

    def test_cash_return_creates_refund_due_and_can_be_completed(self):
        sale = self.sale(mode="CASH", paid=1190, qty=10)
        ret = self.returned(sale, 2)
        self.assertEqual(Decimal(str(ret.debt_reduction)), Decimal("0.000"))
        self.assertEqual(Decimal(str(ret.refund_amount)), Decimal("238.000"))
        self.assertEqual(ret.refund_status, "REFUND_DUE")
        ret.refund_status="REFUNDED"; ret.refund_method="CASH"; ret.refund_date=frappe.utils.today(); ret.save()
        self.assertEqual(frappe.db.get_value("Sale Return", ret.name, "refund_status"), "REFUNDED")
        self.assertEqual(self.balance(), Decimal("0.000"))

    def test_mixed_return_reduces_debt_first_then_refund(self):
        sale = self.sale(mode="MIXED", paid=50, qty=1)
        ret = self.returned(sale, 0.8)
        self.assertEqual(Decimal(str(ret.return_ttc)), Decimal("95.200"))
        self.assertEqual(Decimal(str(ret.debt_reduction)), Decimal("69.000"))
        self.assertEqual(Decimal(str(ret.refund_amount)), Decimal("26.200"))
        self.assertEqual(self.balance(), Decimal("0.000"))

    def test_alternate_uom_and_historical_snapshots(self):
        sale = self.sale(qty=2, uom="BOX")
        self.product.tva_rate=7; self.product.selling_price=999; self.product.uom_conversions[0].conversion_factor=20; self.product.save()
        ret = self.returned(sale, 0.5)
        row=ret.items[0]
        self.assertEqual((Decimal(str(row.conversion_factor)), Decimal(str(row.base_quantity))), (Decimal("12.0"), Decimal("6.0")))
        self.assertEqual((Decimal(str(row.tva_rate)), Decimal(str(row.return_ht)), Decimal(str(row.return_tva))), (Decimal("19.0"), Decimal("50.000"), Decimal("9.500")))
        movement=frappe.db.get_value("Stock Movement", {"source_sale_return_item":row.name}, "quantity")
        self.assertEqual(Decimal(str(movement)), Decimal("6.0"))

    def test_multiple_returns_and_excess_are_rejected(self):
        sale=self.sale(qty=10); self.returned(sale,3); self.returned(sale,4)
        with self.assertRaisesRegex(frappe.ValidationError,"remaining returnable"):
            self.returned(sale,4)
        final = self.returned(sale,3)
        submitted = frappe.get_all("Sale Return", filters={"original_sale": sale.name, "docstatus": 1}, pluck="name")
        self.assertEqual(len(submitted), 3)
        self.assertEqual(frappe.db.count("Credit Transaction", {"source_sale_return": ["in", submitted]}), 3)

    def test_return_and_full_reversal_are_mutually_exclusive(self):
        sale=self.sale(qty=2); self.returned(sale,1)
        reversal=frappe.get_doc({"doctype":"Sale Reversal","business":self.business.name,"original_sale":sale.name,"business_date":frappe.utils.today(),"reason":"No"})
        with self.assertRaisesRegex(frappe.ValidationError,"partial returns"):
            reversal.insert()
        other=self.sale(qty=1)
        reversal=frappe.get_doc({"doctype":"Sale Reversal","business":self.business.name,"original_sale":other.name,"business_date":frappe.utils.today(),"reason":"Full"}).insert(); reversal.submit()
        with self.assertRaisesRegex(frappe.ValidationError,"fully reversed"):
            self.returned(other,1)

    def test_tenant_permissions_and_protected_posting(self):
        sale=self.sale(qty=2)
        owner=self.make_user("OWNER"); staff=self.make_user("STAFF")
        frappe.set_user(staff)
        with self.assertRaises(frappe.PermissionError): self.returned(sale,1)
        forged=frappe.get_doc({"doctype":"Credit Transaction","business":self.business.name,"customer":self.customer.name,"transaction_type":"RETURN","direction":"CREDIT","amount":1,"transaction_date":frappe.utils.today(),"source_sale_return":"fake"})
        with self.assertRaises(frappe.PermissionError): forged.insert()
        frappe.set_user(owner); ret=self.returned(sale,1)
        self.assertEqual(ret.docstatus,1)
        frappe.set_user("Administrator")
        cross=frappe.get_doc({"doctype":"Sale Return","business":self.other_business.name,"original_sale":sale.name,"return_date":frappe.utils.today(),"reason":"Cross","items":[{"original_sale_item":sale.items[0].name,"quantity":1}]})
        with self.assertRaisesRegex(frappe.ValidationError,"Business must match"): cross.insert()

    def test_submission_uses_sale_row_lock_for_concurrent_over_return_protection(self):
        sale = self.sale(qty=2)
        with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
            self.returned(sale, 1)
        lock_calls = [call for call in sql.call_args_list if "FOR UPDATE" in str(call.args[0])]
        self.assertTrue(lock_calls)

    def test_duplicate_posting_and_print(self):
        sale=self.sale(qty=2); ret=self.returned(sale,1)
        self.assertEqual(frappe.db.count("Credit Transaction", {"source_sale_return":ret.name}),1)
        self.assertEqual(frappe.db.count("Stock Movement", {"source_sale_return":ret.name}),1)
        html=frappe.get_print("Sale Return",ret.name,"Sale Return Receipt")
        for value in (sale.invoice_number,"Return Product","119.000","Debt Reduction","Customer return"):
            self.assertIn(str(value),html)
