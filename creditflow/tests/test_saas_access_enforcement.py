from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from creditflow.onboarding import commit_csv
from creditflow.saas_access import get_owner_subscription_summary
from creditflow.subscription import (
    READ_ONLY_MESSAGE, can_use_feature, can_write, clear_request_cache, get_access_state,
    get_entitlement, get_subscription, require_feature,
)


class IntegrationTestSaaSAccessEnforcement(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.token = uuid4().hex[:10]
        self.business = frappe.get_doc({"doctype":"Business","business_name":f"SaaS Access {self.token}"}).insert()
        self.other_business = frappe.get_doc({"doctype":"Business","business_name":f"Other SaaS {self.token}"}).insert()
        self.customer = frappe.get_doc({"doctype":"Customer","business":self.business.name,"customer_name":f"Customer {self.token}"}).insert()
        self.supplier = frappe.get_doc({"doctype":"Supplier","business":self.business.name,"supplier_name":f"Supplier {self.token}"}).insert()
        self.product = frappe.get_doc({"doctype":"Product","business":self.business.name,"product_name":f"Product {self.token}","reference":f"SAAS-{self.token}","primary_unit":"UNIT"}).insert()
        self._stock(20)
        self.owner = self._user("owner", self.business.name, "OWNER")
        self.staff = self._user("staff", self.business.name, "STAFF")
        self.plan = frappe.get_doc({"doctype":"CreditFlow Plan","plan_name":f"Access Plan {self.token}","plan_code":f"ACCESS_{self.token.upper()}","enabled":1,
            "entitlements":[
                {"entitlement_key":"advanced_reports","value_type":"BOOLEAN","value":"true"},
                {"entitlement_key":"migration","value_type":"BOOLEAN","value":"true"},
                {"entitlement_key":"max_users","value_type":"INTEGER","value":"3"},
                {"entitlement_key":"decimal_limit","value_type":"DECIMAL","value":"2.5"},
                {"entitlement_key":"label","value_type":"TEXT","value":"advanced"},
                {"entitlement_key":"config","value_type":"JSON","value":"{\"enabled\": true}"},
            ]}).insert()
        self.subscription = frappe.get_doc({"doctype":"CreditFlow Subscription","business":self.business.name,"plan":self.plan.name,"status":"ACTIVE","subscription_start":now_datetime()}).insert()
        frappe.set_user(self.owner)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _user(self, prefix, business, role):
        email=f"saas-{prefix}-{self.token}@example.com"
        user=frappe.get_doc({"doctype":"User","email":email,"first_name":prefix,"send_welcome_email":0,"creditflow_business":business}).insert(ignore_permissions=True)
        user.add_roles(role); return email

    def _stock(self, qty):
        doc=frappe.get_doc({"doctype":"Stock Movement","business":self.business.name,"product":self.product.name,"direction":"IN","quantity":qty,"movement_reason":"OPENING_STOCK","business_date":"2026-08-25"}).insert(); doc.submit(); return doc

    def _set_status(self, status, expired_trial=False):
        frappe.set_user("Administrator"); doc=frappe.get_doc("CreditFlow Subscription",self.subscription.name)
        doc.status=status
        if status=="TRIAL":
            doc.trial_start=add_days(now_datetime(),-15 if expired_trial else -1)
            doc.trial_end=add_days(now_datetime(),-1 if expired_trial else 13)
        doc.save(); clear_request_cache(); frappe.set_user(self.owner)

    def _sale(self):
        return frappe.get_doc({"doctype":"Sale","business":self.business.name,"customer":self.customer.name,"business_date":"2026-08-26","sale_mode":"CREDIT","items":[{"product":self.product.name,"quantity":1,"unit_price":10}]}).insert()

    def _purchase(self):
        return frappe.get_doc({"doctype":"Purchase","business":self.business.name,"supplier":self.supplier.name,"business_date":"2026-08-26","payment_status":"CREDIT","items":[{"product":self.product.name,"quantity":1,"unit_cost":5}]}).insert()

    def assert_readonly(self, callback):
        with self.assertRaisesRegex(frappe.PermissionError, "currently read-only"):
            callback()

    def test_01_status_matrix_and_trial_expiry_are_computed_at_request_time(self):
        for status, expected in (("ACTIVE",True),("PAST_DUE",False),("EXPIRED",False),("CANCELLED",False)):
            self._set_status(status); self.assertEqual(can_write(),expected,status)
        self._set_status("TRIAL"); self.assertEqual(get_access_state(),"TRIAL"); self.assertTrue(can_write())
        self._set_status("TRIAL",expired_trial=True); self.assertEqual(get_access_state(),"EXPIRED"); self.assertFalse(can_write())

    def test_02_valid_trial_and_active_can_create_sale(self):
        self._set_status("TRIAL"); self.assertTrue(self._sale().name)
        self._set_status("ACTIVE"); self.assertTrue(self._sale().name)

    def test_03_all_readonly_states_block_sale_but_preserve_read(self):
        self._set_status("ACTIVE"); sale=self._sale()
        for status in ("PAST_DUE","EXPIRED","CANCELLED"):
            self._set_status(status); self.assert_readonly(self._sale)
            self.assertEqual(frappe.get_doc("Sale",sale.name).name,sale.name)

    def test_04_expired_blocks_purchase_payment_supplier_payment_and_stock(self):
        self._set_status("EXPIRED")
        attempts=(self._purchase,
            lambda: frappe.get_doc({"doctype":"Payment","business":self.business.name,"customer":self.customer.name,"amount":1,"business_date":"2026-08-26","payment_method":"CASH"}).insert(),
            lambda: frappe.get_doc({"doctype":"Supplier Payment","business":self.business.name,"supplier":self.supplier.name,"amount":1,"business_date":"2026-08-26","payment_method":"CASH"}).insert(),
            lambda: self._stock(1))
        for attempt in attempts: self.assert_readonly(attempt)

    def test_05_expired_blocks_reversals_and_import_commit(self):
        self._set_status("ACTIVE"); sale=self._sale(); sale.submit(); purchase=self._purchase(); purchase.submit()
        self._set_status("EXPIRED")
        self.assert_readonly(lambda: frappe.get_doc({"doctype":"Sale Reversal","business":self.business.name,"original_sale":sale.name,"business_date":"2026-08-27","reason":"blocked"}).insert())
        self.assert_readonly(lambda: frappe.get_doc({"doctype":"Purchase Reversal","business":self.business.name,"original_purchase":purchase.name,"business_date":"2026-08-27","reason":"blocked"}).insert())
        self.assert_readonly(lambda: commit_csv("CUSTOMERS",f"customer_name\nBlocked {self.token}\n"))

    def test_06_direct_insert_save_and_submit_bypasses_are_rejected(self):
        self._set_status("ACTIVE"); sale=self._sale(); customer=frappe.get_doc("Customer",self.customer.name)
        self._set_status("EXPIRED")
        self.assert_readonly(self._sale)
        customer.phone="999"; self.assert_readonly(customer.save)
        self.assert_readonly(sale.submit)

    def test_07_forbidden_attempts_have_no_economic_or_stock_side_effects(self):
        self._set_status("ACTIVE"); baseline=self._sale(); baseline.submit()
        doctypes=("Sale","Purchase","Payment","Supplier Payment","Credit Transaction","Supplier Transaction","Stock Movement")
        before={dt:frappe.db.count(dt,{"business":self.business.name}) for dt in doctypes}
        self._set_status("EXPIRED")
        for attempt in (self._sale,self._purchase,lambda:self._stock(2)):
            self.assert_readonly(attempt)
        after={dt:frappe.db.count(dt,{"business":self.business.name}) for dt in doctypes}
        self.assertEqual(before,after)

    def test_08_switching_back_to_active_restores_writes_without_repair(self):
        self._set_status("EXPIRED"); self.assert_readonly(self._sale)
        self._set_status("ACTIVE"); self.assertTrue(self._sale().name)

    def test_09_legacy_business_without_subscription_remains_writable(self):
        frappe.set_user("Administrator"); legacy=frappe.get_doc({"doctype":"Business","business_name":f"Legacy {self.token}"}).insert(); user=self._user("legacy",legacy.name,"OWNER")
        frappe.set_user(user); self.assertEqual(get_access_state(),"LEGACY_ACCESS"); self.assertTrue(can_write())
        self.assertTrue(frappe.get_doc({"doctype":"Customer","business":legacy.name,"customer_name":"Legacy Customer"}).insert().name)

    def test_10_typed_entitlements_and_missing_behavior(self):
        self.assertIs(get_entitlement(self.business.name,"advanced_reports"),True)
        self.assertEqual(get_entitlement(self.business.name,"max_users"),3)
        self.assertEqual(get_entitlement(self.business.name,"decimal_limit"),Decimal("2.5"))
        self.assertEqual(get_entitlement(self.business.name,"label"),"advanced")
        self.assertEqual(get_entitlement(self.business.name,"config"),{"enabled":True})
        self.assertIsNone(get_entitlement(self.business.name,"missing")); self.assertFalse(can_use_feature(self.business.name,"missing"))
        self.assertTrue(require_feature(self.business.name,"advanced_reports"))

    def test_11_invalid_and_duplicate_entitlements_are_rejected(self):
        frappe.set_user("Administrator")
        for kind,value in (("BOOLEAN","maybe"),("INTEGER","1.2"),("DECIMAL","NaN"),("JSON","{")):
            with self.subTest(kind=kind):
                plan=frappe.get_doc({"doctype":"CreditFlow Plan","plan_name":"Bad","plan_code":f"BAD_{kind}_{self.token.upper()}","enabled":1,"entitlements":[{"entitlement_key":"bad","value_type":kind,"value":value}]})
                with self.assertRaises(frappe.ValidationError): plan.insert()
        duplicate=frappe.get_doc({"doctype":"CreditFlow Plan","plan_name":"Dup","plan_code":f"DUP_{self.token.upper()}","enabled":1,"entitlements":[{"entitlement_key":"x","value_type":"BOOLEAN","value":"true"},{"entitlement_key":"X","value_type":"BOOLEAN","value":"false"}]})
        with self.assertRaisesRegex(frappe.ValidationError,"Duplicate"): duplicate.insert()

    def test_12_max_users_counts_enabled_owner_and_staff_only(self):
        third=frappe.get_doc({"doctype":"User","email":f"third-{self.token}@example.com","first_name":"Third","send_welcome_email":0,"creditflow_business":self.business.name}).insert(ignore_permissions=True); third.add_roles("STAFF")
        fourth=frappe.get_doc({"doctype":"User","email":f"fourth-{self.token}@example.com","first_name":"Fourth","send_welcome_email":0,"creditflow_business":self.business.name}).insert(ignore_permissions=True)
        with self.assertRaisesRegex(frappe.PermissionError,"maximum of 3"): fourth.add_roles("STAFF")

    def test_13_business_cannot_borrow_other_active_subscription_or_entitlements(self):
        frappe.set_user("Administrator")
        other_plan=frappe.get_doc({"doctype":"CreditFlow Plan","plan_name":"Other","plan_code":f"OTHER_{self.token.upper()}","enabled":1,"entitlements":[{"entitlement_key":"advanced_reports","value_type":"BOOLEAN","value":"true"}]}).insert()
        frappe.get_doc({"doctype":"CreditFlow Subscription","business":self.other_business.name,"plan":other_plan.name,"status":"ACTIVE"}).insert()
        self._set_status("EXPIRED")
        with self.assertRaises(frappe.PermissionError): get_subscription(self.other_business.name)
        with self.assertRaises(frappe.PermissionError): get_entitlement(self.other_business.name,"advanced_reports")
        self.assertFalse(can_write())

    def test_14_owner_and_staff_cannot_forge_subscription_state(self):
        self._set_status("EXPIRED")
        for user in (self.owner,self.staff):
            frappe.set_user(user); doc=frappe.get_doc("CreditFlow Subscription",self.subscription.name); doc.status="ACTIVE"
            with self.assertRaises(frappe.PermissionError): doc.save()
        frappe.set_user("Administrator"); doc=frappe.get_doc("CreditFlow Subscription",self.subscription.name); doc.status="ACTIVE"; doc.save(); self.assertEqual(doc.status,"ACTIVE")

    def test_15_subscription_summary_is_owner_scoped(self):
        summary=get_owner_subscription_summary(); self.assertEqual(summary["business"],self.business.name); self.assertEqual(summary["plan_code"],self.plan.plan_code)
        frappe.set_user(self.staff)
        with self.assertRaises(frappe.PermissionError): get_owner_subscription_summary()

    def test_16_administrator_override_does_not_delete_or_change_data(self):
        self._set_status("EXPIRED"); frappe.set_user("Administrator")
        before=frappe.db.count("Customer",{"business":self.business.name})
        frappe.get_doc({"doctype":"Customer","business":self.business.name,"customer_name":f"Admin {self.token}"}).insert()
        self.assertEqual(frappe.db.count("Customer",{"business":self.business.name}),before+1)
