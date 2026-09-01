from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from creditflow.subscription import (
    create_trial_subscription,
    get_entitlement,
    get_plan,
    get_subscription,
    is_subscription_active,
    is_trial_active,
    require_active_subscription,
    require_entitlement,
)


class IntegrationTestSubscriptionFoundation(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.token = uuid4().hex[:10]
        self.business_a = frappe.get_doc({"doctype":"Business","business_name":f"Subscription A {self.token}"}).insert()
        self.business_b = frappe.get_doc({"doctype":"Business","business_name":f"Subscription B {self.token}"}).insert()
        self.owner_a = self.make_user("owner", self.business_a.name, "OWNER")
        self.staff_a = self.make_user("staff", self.business_a.name, "STAFF")
        self.plan = frappe.get_doc({
            "doctype":"CreditFlow Plan","plan_name":f"Test Plan {self.token}","plan_code":f"TEST_{self.token.upper()}",
            "monthly_price":"29.900","annual_price":"299.000","enabled":1,"description":"Data-driven test plan",
            "entitlements":[
                {"entitlement_key":"advanced_reports","value_type":"BOOLEAN","value":"true"},
                {"entitlement_key":"maximum_users","value_type":"INTEGER","value":"10"},
                {"entitlement_key":"automation_config","value_type":"JSON","value":"{\"jobs\": 5}"},
            ],
        }).insert()

    def tearDown(self):
        frappe.set_user("Administrator")

    def make_user(self, prefix, business, role):
        user = frappe.get_doc({"doctype":"User","email":f"subscription-{prefix}-{self.token}@example.com","first_name":prefix,"send_welcome_email":0,"creditflow_business":business}).insert(ignore_permissions=True)
        user.add_roles(role)
        return user.name

    def subscription(self, business=None, status="ACTIVE", **values):
        data={"doctype":"CreditFlow Subscription","business":business or self.business_a.name,"plan":self.plan.name,"status":status}
        data.update(values)
        return frappe.get_doc(data).insert()

    def test_plan_creation_and_typed_entitlements(self):
        self.assertEqual(self.plan.name, f"TEST_{self.token.upper()}")
        self.assertEqual(Decimal(str(self.plan.monthly_price)), Decimal("29.900"))
        self.subscription()
        self.assertEqual(get_plan(self.business_a.name).name, self.plan.name)
        self.assertIs(get_entitlement(self.business_a.name,"advanced_reports"), True)
        self.assertEqual(get_entitlement(self.business_a.name,"maximum_users"), 10)
        self.assertEqual(get_entitlement(self.business_a.name,"automation_config"), {"jobs":5})
        self.assertIsNone(get_entitlement(self.business_a.name,"not_defined"))

    def test_default_fourteen_day_trial_creation_and_active_state(self):
        trial=create_trial_subscription(self.business_a.name,self.plan.name)
        self.assertEqual(trial.status,"TRIAL")
        self.assertEqual((trial.trial_end-trial.trial_start).days,14)
        self.assertTrue(is_trial_active(self.business_a.name))
        self.assertTrue(is_subscription_active(self.business_a.name))

    def test_trial_expired_after_trial_end(self):
        now=now_datetime();self.subscription(status="TRIAL",trial_start=add_days(now,-15),trial_end=add_days(now,-1))
        self.assertFalse(is_trial_active(self.business_a.name))
        self.assertFalse(is_subscription_active(self.business_a.name))

    def test_active_subscription_and_require_helpers(self):
        subscription=self.subscription(status="ACTIVE",subscription_start=now_datetime())
        self.assertTrue(is_subscription_active(self.business_a.name))
        self.assertEqual(require_active_subscription(self.business_a.name).name,subscription.name)
        self.assertIs(require_entitlement(self.business_a.name,"advanced_reports"),True)
        self.assertEqual(require_entitlement(self.business_a.name,"maximum_users"),10)
        with self.assertRaises(frappe.PermissionError):require_entitlement(self.business_a.name,"ecommerce")

    def test_inactive_statuses_are_recognized_without_deleting_data(self):
        for status in ("PAST_DUE","EXPIRED","CANCELLED"):
            subscription=self.subscription(status=status)
            self.assertEqual(get_subscription(self.business_a.name).status,status)
            self.assertFalse(is_subscription_active(self.business_a.name))
            with self.assertRaises(frappe.PermissionError):require_active_subscription(self.business_a.name)
            frappe.delete_doc("CreditFlow Subscription",subscription.name,force=True)

    def test_only_one_subscription_per_business(self):
        self.subscription()
        with self.assertRaises((frappe.ValidationError, frappe.DuplicateEntryError)):self.subscription(status="TRIAL",trial_start=now_datetime(),trial_end=add_days(now_datetime(),14))

    def test_business_a_cannot_query_or_modify_business_b_subscription(self):
        subscription_b=self.subscription(self.business_b.name)
        frappe.set_user(self.owner_a)
        with self.assertRaises(frappe.PermissionError):get_subscription(self.business_b.name)
        with self.assertRaises(frappe.PermissionError):frappe.get_doc("CreditFlow Subscription",subscription_b.name).check_permission("read")
        forged=frappe.get_doc("CreditFlow Subscription",subscription_b.name);forged.status="CANCELLED"
        with self.assertRaises(frappe.PermissionError):forged.save()
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription",subscription_b.name,"status"),"ACTIVE")

    def test_owner_can_read_own_subscription_but_not_mutate_state(self):
        subscription=self.subscription()
        frappe.set_user(self.owner_a)
        self.assertEqual(get_subscription().name,subscription.name)
        doc=frappe.get_doc("CreditFlow Subscription",subscription.name);doc.status="CANCELLED"
        with self.assertRaises(frappe.PermissionError):doc.save()

    def test_staff_cannot_read_or_modify_subscription_or_plan_configuration(self):
        subscription=self.subscription()
        frappe.set_user(self.staff_a)
        self.assertFalse(frappe.has_permission("CreditFlow Subscription","write",subscription.name))
        self.assertFalse(frappe.has_permission("CreditFlow Plan","write",self.plan.name))
        with self.assertRaises(frappe.PermissionError):frappe.get_doc("CreditFlow Subscription",subscription.name).check_permission("read")
        subscription.status="CANCELLED"
        with self.assertRaises(frappe.PermissionError):subscription.save()

    def test_subscription_changes_do_not_touch_erp_economic_data(self):
        before={doctype:frappe.db.count(doctype,{"business":self.business_a.name}) for doctype in ("Credit Transaction","Supplier Transaction","Stock Movement","Sale","Purchase","Payment","Supplier Payment")}
        subscription=self.subscription(status="ACTIVE")
        for status in ("PAST_DUE","EXPIRED","CANCELLED"):
            subscription.status=status;subscription.save()
        after={doctype:frappe.db.count(doctype,{"business":self.business_a.name}) for doctype in before}
        self.assertEqual(before,after)

    def test_provider_fields_are_optional_and_gateway_neutral(self):
        subscription=self.subscription(status="ACTIVE")
        self.assertFalse(subscription.external_provider)
        subscription.external_provider="future-provider";subscription.external_customer_id="customer-1";subscription.external_subscription_id="subscription-1";subscription.save()
        self.assertEqual(frappe.db.get_value("CreditFlow Subscription",subscription.name,"external_provider"),"future-provider")
