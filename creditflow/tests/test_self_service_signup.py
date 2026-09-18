from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_datetime
from frappe.utils.password import check_password

from creditflow.permissions import has_permission
from creditflow.signup import create_self_service_tenant, signup
from creditflow.subscription import DEFAULT_TRIAL_DAYS, is_trial_active


class IntegrationTestSelfServiceSignup(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.token = uuid4().hex[:12]
        self.email = f"signup-{self.token}@example.com"
        self.password = f"Strong-{self.token}-Password!42"

    def tearDown(self):
        frappe.set_user("Administrator")

    def payload(self, **updates):
        values = {
            "first_name": "Amina",
            "last_name": "Ben Salah",
            "email": self.email,
            "password": self.password,
            "business_name": f"Signup Business {self.token}",
            "phone": "+216 20 000 000",
        }
        values.update(updates)
        return values

    def onboard(self, **updates):
        return create_self_service_tenant(**self.payload(**updates))

    def test_01_valid_signup_creates_owner_business_and_trial(self):
        result = self.onboard()
        self.assertTrue(result["created"])
        self.assertEqual(result["redirect_to"], "/desk/kivo")
        self.assertTrue(check_password(self.email, self.password))

        user = frappe.get_doc("User", self.email)
        business = frappe.get_doc("Business", result["business"])
        subscription = frappe.get_doc("CreditFlow Subscription", result["subscription"])
        self.assertIn("OWNER", frappe.get_roles(user.name))
        self.assertNotIn("System Manager", frappe.get_roles(user.name))
        self.assertEqual(user.creditflow_business, business.name)
        self.assertEqual(business.onboarding_status, "PROFILE_PENDING")
        self.assertEqual(subscription.status, "TRIAL")
        self.assertEqual(subscription.plan, "STARTER")
        self.assertEqual(
            get_datetime(subscription.trial_end) - get_datetime(subscription.trial_start),
            timedelta(days=DEFAULT_TRIAL_DAYS),
        )

    def test_02_duplicate_unrelated_email_creates_no_tenant(self):
        frappe.get_doc({
            "doctype": "User", "email": self.email, "first_name": "Existing", "send_welcome_email": 0,
        }).insert(ignore_permissions=True)
        business_count = frappe.db.count("Business")
        subscription_count = frappe.db.count("CreditFlow Subscription")
        with self.assertRaisesRegex(frappe.ValidationError, "Unable to complete signup"):
            self.onboard()
        self.assertEqual(frappe.db.count("Business"), business_count)
        self.assertEqual(frappe.db.count("CreditFlow Subscription"), subscription_count)

    def test_03_double_submit_is_idempotent(self):
        first = self.onboard()
        second = self.onboard()
        self.assertFalse(second["created"])
        self.assertEqual(second["user"], first["user"])
        self.assertEqual(second["business"], first["business"])
        self.assertEqual(second["subscription"], first["subscription"])
        self.assertEqual(frappe.db.count("CreditFlow Subscription", {"business": first["business"]}), 1)
        self.assertEqual(frappe.db.count("Business", {"email": self.email}), 1)
        with self.assertRaisesRegex(frappe.ValidationError, "Unable to complete signup"):
            self.onboard(password="Wrong-retry-password!42")

    def test_04_business_creation_failure_rolls_back_user(self):
        business_count = frappe.db.count("Business")
        with patch("creditflow.signup._create_business", side_effect=frappe.ValidationError("forced business failure")):
            with self.assertRaisesRegex(frappe.ValidationError, "forced business failure"):
                self.onboard()
        self.assertFalse(frappe.db.exists("User", self.email))
        self.assertEqual(frappe.db.count("Business"), business_count)
        self.assertFalse(frappe.db.exists("CreditFlow Subscription", {"owner": self.email}))

    def test_05_subscription_failure_rolls_back_user_and_business(self):
        business_count = frappe.db.count("Business")
        with patch("creditflow.signup.create_trial_subscription", side_effect=frappe.ValidationError("forced trial failure")):
            with self.assertRaisesRegex(frappe.ValidationError, "forced trial failure"):
                self.onboard()
        self.assertFalse(frappe.db.exists("User", self.email))
        self.assertEqual(frappe.db.count("Business"), business_count)
        self.assertFalse(frappe.db.exists("CreditFlow Subscription", {"owner": self.email}))

    def test_06_role_escalation_field_is_rejected(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Unsupported signup fields"):
            self.onboard(roles=["System Manager"])
        self.assertFalse(frappe.db.exists("User", self.email))

    def test_07_existing_business_assignment_is_rejected(self):
        other = frappe.get_doc({"doctype": "Business", "business_name": f"Other {self.token}"}).insert()
        with self.assertRaisesRegex(frappe.ValidationError, "Unsupported signup fields"):
            self.onboard(business=other.name)
        self.assertFalse(frappe.db.exists("User", self.email))
        self.assertEqual(frappe.db.count("CreditFlow Subscription", {"business": other.name}), 0)

    def test_08_subscription_status_is_server_authoritative(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Unsupported signup fields"):
            self.onboard(status="ACTIVE")
        self.assertFalse(frappe.db.exists("User", self.email))

    def test_09_trial_duration_is_server_authoritative(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Unsupported signup fields"):
            self.onboard(trial_days=365)
        result = self.onboard()
        subscription = frappe.get_doc("CreditFlow Subscription", result["subscription"])
        self.assertEqual(
            get_datetime(subscription.trial_end) - get_datetime(subscription.trial_start), timedelta(days=14)
        )

    def test_10_owner_can_access_own_business_and_trial_is_active(self):
        result = self.onboard()
        frappe.set_user(self.email)
        business = frappe.get_doc("Business", result["business"])
        self.assertTrue(business.has_permission("read"))
        self.assertTrue(is_trial_active())

    def test_11_owner_cannot_access_other_business(self):
        result = self.onboard()
        other = frappe.get_doc({"doctype": "Business", "business_name": f"Foreign {self.token}"}).insert(ignore_permissions=True)
        frappe.set_user(self.email)
        self.assertFalse(frappe.get_doc("Business", other.name).has_permission("read"))
        self.assertFalse(has_permission(frappe.get_doc("Business", other.name), "read", self.email))
        self.assertTrue(frappe.get_doc("Business", result["business"]).has_permission("read"))

    def test_12_only_public_signup_function_is_whitelisted(self):
        from creditflow import signup as signup_module

        self.assertIn(signup, frappe.whitelisted)
        for internal in (
            signup_module.create_self_service_tenant,
            signup_module._create_user,
            signup_module._create_business,
            signup_module._assign_owner,
        ):
            self.assertNotIn(internal, frappe.whitelisted)

    def test_13_plaintext_password_is_not_persisted(self):
        result = self.onboard()
        for doctype, name in (
            ("User", self.email),
            ("Business", result["business"]),
            ("CreditFlow Subscription", result["subscription"]),
        ):
            self.assertNotIn(self.password, frappe.as_json(frappe.get_doc(doctype, name).as_dict()))
        stored = frappe.db.sql(
            "SELECT password FROM `__Auth` WHERE doctype=%s AND name=%s AND fieldname=%s",
            ("User", self.email, "password"),
        )[0][0]
        self.assertTrue(stored)
        self.assertNotEqual(stored, self.password)

    def test_14_existing_businesses_and_users_remain_unchanged(self):
        existing_business = frappe.get_doc({"doctype": "Business", "business_name": f"Legacy {self.token}"}).insert()
        existing_user = frappe.get_doc({
            "doctype": "User", "email": f"legacy-{self.token}@example.com", "first_name": "Legacy",
            "send_welcome_email": 0,
        }).insert(ignore_permissions=True)
        before_business = frappe.get_doc("Business", existing_business.name).as_dict()
        before_user_business = existing_user.creditflow_business
        self.onboard()
        after = frappe.get_doc("Business", existing_business.name)
        self.assertEqual(after.business_name, before_business.business_name)
        self.assertEqual(after.onboarding_status, before_business.onboarding_status)
        self.assertEqual(frappe.db.get_value("User", existing_user.name, "creditflow_business"), before_user_business)
        self.assertFalse(frappe.db.exists("CreditFlow Subscription", {"business": existing_business.name}))

    def test_15_public_input_contract_rejects_plan_and_permissions(self):
        for field, value in (
            ("plan", "PRO"), ("subscription_plan", "BUSINESS"),
            ("creditflow_business", "BUS-B"), ("role_profile_name", "System Manager"),
        ):
            with self.subTest(field=field):
                payload = self.payload()
                payload[field] = value
                with self.assertRaisesRegex(frappe.ValidationError, "Unsupported signup fields"):
                    create_self_service_tenant(**payload)
        self.assertFalse(frappe.db.exists("User", self.email))
