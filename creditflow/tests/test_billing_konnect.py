from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_months, get_datetime, now_datetime

from creditflow.billing.service import (
    create_checkout, reconcile_owner_payment, reconcile_provider_payment, tnd_to_millimes,
)


class FakeKonnect:
    checkout_calls=[]; details={}; outage=False; wallet_id="wallet-test"
    def create_checkout(self,payment,customer):
        self.__class__.checkout_calls.append((payment.amount_minor,payment.internal_order_id,payment.plan))
        return {"provider_payment_id":f"payref{payment.internal_order_id[3:]}","checkout_url":"https://sandbox.example/pay"}
    def get_payment_status(self,ref):
        if self.__class__.outage: raise TimeoutError("provider timeout")
        return dict(self.__class__.details[ref])


class IntegrationTestKonnectBilling(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator"); self.token=uuid4().hex[:10]
        self.business=frappe.get_doc({"doctype":"Business","business_name":f"Billing {self.token}"}).insert()
        self.other=frappe.get_doc({"doctype":"Business","business_name":f"Other Billing {self.token}"}).insert()
        self.owner=self.user("owner",self.business.name,"OWNER"); self.staff=self.user("staff",self.business.name,"STAFF")
        self.plan=frappe.get_doc({"doctype":"CreditFlow Plan","plan_name":f"Billing Plan {self.token}","plan_code":f"BILL_{self.token.upper()}","enabled":1,"monthly_price":"59.000","annual_price":"590.000"}).insert()
        now=now_datetime(); self.subscription=frappe.get_doc({"doctype":"CreditFlow Subscription","business":self.business.name,"plan":self.plan.name,"status":"TRIAL","trial_start":now,"trial_end":add_days(now,14)}).insert()
        FakeKonnect.checkout_calls=[];FakeKonnect.details={};FakeKonnect.outage=False
        self.provider_patch=patch.dict("creditflow.billing.service.PROVIDERS",{"KONNECT":FakeKonnect});self.provider_patch.start();frappe.set_user(self.owner)
    def tearDown(self): self.provider_patch.stop();frappe.set_user("Administrator")
    def user(self,prefix,business,role):
        email=f"billing-{prefix}-{self.token}@example.com";u=frappe.get_doc({"doctype":"User","email":email,"first_name":prefix,"send_welcome_email":0,"creditflow_business":business}).insert(ignore_permissions=True);u.add_roles(role);return email
    def checkout(self,cycle="MONTHLY"):
        result=create_checkout(self.plan.name,cycle);payment=frappe.get_doc("CreditFlow Billing Payment",result["payment"]);return result,payment
    def completed(self,payment,**changes):
        data={"normalized_status":"SUCCEEDED","provider_status":"completed","amount_minor":tnd_to_millimes(payment.amount),"currency":"TND","order_id":payment.internal_order_id,"receiver_wallet_id":"wallet-test","transaction_success":True,"payment_id":payment.provider_payment_id};data.update(changes);FakeKonnect.details[payment.provider_payment_id]=data;return data

    def test_01_owner_checkout_is_server_priced_and_pending(self):
        result,p=self.checkout();self.assertEqual(p.status,"PENDING");self.assertEqual(p.amount,59);self.assertEqual(p.currency,"TND");self.assertEqual(FakeKonnect.checkout_calls[0][0],59000);self.assertEqual(result["checkout_url"],"https://sandbox.example/pay");self.assertTrue(p.provider_payment_id)
    def test_02_staff_and_authority_injection_are_rejected(self):
        frappe.set_user(self.staff)
        with self.assertRaises(frappe.PermissionError):create_checkout(self.plan.name)
        frappe.set_user(self.owner)
        for attack in ({"business":self.other.name},{"amount":"0.001"},{"currency":"USD"},{"trial_days":365},{"provider_payment_id":"fake"}):
            with self.assertRaisesRegex(frappe.ValidationError,"Unsupported"):create_checkout(self.plan.name,**attack)
    def test_03_disabled_unknown_plan_and_invalid_cycle_are_rejected(self):
        frappe.set_user("Administrator");self.plan.enabled=0;self.plan.save();frappe.set_user(self.owner)
        with self.assertRaises(frappe.ValidationError):create_checkout(self.plan.name)
        with self.assertRaises(frappe.DoesNotExistError):create_checkout("NO-SUCH-PLAN")
    def test_04_exact_tnd_millime_conversion(self):
        for amount,minor in (("29.000",29000),("49.000",49000),("59.000",59000),("59.500",59500)):self.assertEqual(tnd_to_millimes(amount),minor)
        for bad in ("0","-1","1.0001","NaN"):
            with self.assertRaises(frappe.ValidationError):tnd_to_millimes(bad)
    def test_05_fake_unknown_webhook_and_browser_values_do_not_activate(self):
        _,p=self.checkout();self.assertEqual(reconcile_provider_payment("unknownref99")["status"],"UNKNOWN");self.assertEqual(frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"status"),"TRIAL");self.assertEqual(p.status,"PENDING")
    def test_06_verified_success_activates_subscription(self):
        _,p=self.checkout();self.completed(p);result=reconcile_provider_payment(p.provider_payment_id);sub=frappe.get_doc("CreditFlow Subscription",self.subscription.name);p.reload();self.assertEqual(result["status"],"SUCCEEDED");self.assertEqual(sub.status,"ACTIVE");self.assertEqual(sub.plan,self.plan.name);self.assertEqual(sub.external_provider,"KONNECT");self.assertTrue(sub.current_period_end);self.assertEqual(p.status,"SUCCEEDED")
    def test_07_amount_currency_order_wallet_and_transaction_mismatch_never_activate(self):
        fields=(("amount_minor",1),("currency","USD"),("order_id","forged"),("receiver_wallet_id","other-wallet"),("transaction_success",False))
        for field,value in fields:
            with self.subTest(field=field):
                _,p=self.checkout();self.completed(p,**{field:value});self.assertEqual(reconcile_provider_payment(p.provider_payment_id)["status"],"PENDING");self.assertEqual(frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"status"),"TRIAL")
    def test_08_failed_expired_pending_do_not_activate(self):
        for normalized in ("FAILED","EXPIRED","PENDING"):
            _,p=self.checkout();FakeKonnect.details[p.provider_payment_id]={"normalized_status":normalized,"provider_status":normalized.lower(),"amount_minor":59000,"currency":"TND","order_id":p.internal_order_id};result=reconcile_provider_payment(p.provider_payment_id);self.assertEqual(result["status"],normalized);self.assertEqual(frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"status"),"TRIAL")
    def test_09_duplicate_and_replayed_success_extend_exactly_once(self):
        _,p=self.checkout();self.completed(p);reconcile_provider_payment(p.provider_payment_id);end=frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"current_period_end");reconcile_provider_payment(p.provider_payment_id);self.assertEqual(frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"current_period_end"),end)
    def test_10_early_renewal_preserves_paid_time(self):
        _,first=self.checkout();self.completed(first);reconcile_provider_payment(first.provider_payment_id);old_end=get_datetime(frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"current_period_end"));_,renewal=self.checkout();self.completed(renewal);reconcile_provider_payment(renewal.provider_payment_id);new_end=get_datetime(frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"current_period_end"));self.assertEqual(new_end,add_months(old_end,1))
    def test_11_failed_checkout_preserves_existing_active_period(self):
        _,p=self.checkout();self.completed(p);reconcile_provider_payment(p.provider_payment_id);old=frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"current_period_end");_,failed=self.checkout();FakeKonnect.details[failed.provider_payment_id]={"normalized_status":"FAILED","provider_status":"pending"};reconcile_provider_payment(failed.provider_payment_id);self.assertEqual(frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"current_period_end"),old);self.assertEqual(frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"status"),"ACTIVE")
    def test_12_reconciliation_recovers_missed_webhook(self):
        _,p=self.checkout();self.completed(p);result=reconcile_owner_payment(p.name);self.assertEqual(result["status"],"SUCCEEDED")
    def test_13_provider_outage_keeps_pending_and_subscription_unchanged(self):
        _,p=self.checkout();FakeKonnect.outage=True;result=reconcile_provider_payment(p.provider_payment_id);self.assertEqual(result["status"],"PENDING");self.assertEqual(frappe.db.get_value("CreditFlow Subscription",self.subscription.name,"status"),"TRIAL")
    def test_14_cross_tenant_payment_access_is_denied(self):
        _,p=self.checkout();frappe.set_user("Administrator");other_owner=self.user("other",self.other.name,"OWNER");frappe.set_user(other_owner)
        with self.assertRaises(frappe.PermissionError):reconcile_owner_payment(p.name)
        self.assertFalse(frappe.get_doc("CreditFlow Billing Payment",p.name).has_permission("read"))
    def test_15_owner_cannot_forge_success_or_active(self):
        _,p=self.checkout();p.status="SUCCEEDED"
        with self.assertRaises(frappe.PermissionError):p.save()
        sub=frappe.get_doc("CreditFlow Subscription",self.subscription.name);sub.status="ACTIVE"
        with self.assertRaises(frappe.PermissionError):sub.save()
    def test_16_midperiod_plan_change_is_rejected(self):
        _,p=self.checkout();self.completed(p);reconcile_provider_payment(p.provider_payment_id);frappe.set_user("Administrator");other=frappe.get_doc({"doctype":"CreditFlow Plan","plan_name":"Other","plan_code":f"CHANGE_{self.token.upper()}","enabled":1,"monthly_price":10}).insert();frappe.set_user(self.owner)
        with self.assertRaisesRegex(frappe.ValidationError,"current paid period"):create_checkout(other.name)
    def test_17_checkout_provider_failure_retains_pending_history(self):
        with patch.object(FakeKonnect,"create_checkout",side_effect=TimeoutError("down")):
            result=create_checkout(self.plan.name)
        self.assertEqual(result["status"],"PENDING");self.assertTrue(frappe.db.exists("CreditFlow Billing Payment",result["payment"]));self.assertIsNone(result["checkout_url"])
