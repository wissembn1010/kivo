from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

import creditflow.signup as signup_service
from creditflow.saas_onboarding import complete_business_profile, get_creditflow_home_page


class IntegrationTestVerifiedSignupOnboarding(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.token_id = uuid4().hex[:12]
        self.email = f"verified-{self.token_id}@example.com"
        self.password = f"Secure-{self.token_id}!42"
        self.sent_tokens = []
        self.mail_patch = patch("creditflow.signup._send_verification_email", side_effect=self._capture_mail)
        self.mail_patch.start()

    def tearDown(self):
        self.mail_patch.stop()
        frappe.set_user("Administrator")

    def _capture_mail(self, email, token):
        self.sent_tokens.append((email, token))

    def payload(self, **updates):
        data = {"first_name":"Amina", "last_name":"Trabelsi", "email":self.email,
                "business_name":f"Verified Business {self.token_id}", "phone":"+21620000000"}
        data.update(updates)
        return data

    def request(self, **updates):
        result = signup_service.request_signup(**self.payload(**updates))
        token = self.sent_tokens[-1][1] if self.sent_tokens else None
        return result, token

    def verify(self, token=None, password=None):
        return signup_service.verify_and_provision(token or self.sent_tokens[-1][1], password or self.password)

    def test_01_request_creates_only_pending_state(self):
        result, token = self.request()
        self.assertTrue(result["ok"]); self.assertTrue(token)
        pending = frappe.get_doc("CreditFlow Pending Signup", {"email": self.email})
        self.assertEqual(pending.status, "PENDING")
        self.assertNotEqual(pending.token_hash, token)
        self.assertEqual(len(pending.token_hash), 64)
        self.assertFalse(frappe.db.exists("User", self.email))
        self.assertFalse(frappe.db.exists("Business", {"email": self.email}))
        self.assertFalse(frappe.db.exists("CreditFlow Subscription", {"owner": self.email}))

    def test_02_valid_token_provisions_exactly_one_tenant_and_trial(self):
        _, token = self.request(); result = self.verify(token)
        pending = frappe.get_doc("CreditFlow Pending Signup", {"email": self.email})
        user = frappe.get_doc("User", self.email)
        subscription = frappe.get_doc("CreditFlow Subscription", pending.subscription)
        self.assertTrue(result["ok"]); self.assertEqual(pending.status, "PROVISIONED")
        self.assertFalse(pending.token_hash); self.assertTrue(pending.consumed_token_hash)
        self.assertIn("OWNER", frappe.get_roles(self.email)); self.assertNotIn("System Manager", frappe.get_roles(self.email))
        self.assertEqual(user.creditflow_business, pending.business)
        self.assertEqual(subscription.plan, "STARTER"); self.assertEqual(subscription.status, "TRIAL")
        self.assertEqual(get_datetime(subscription.trial_end)-get_datetime(subscription.trial_start), timedelta(days=14))
        self.assertEqual(frappe.db.count("Business", {"email": self.email}), 1)
        self.assertEqual(frappe.db.count("CreditFlow Subscription", {"business": pending.business}), 1)

    def test_03_invalid_and_cross_signup_tokens_are_rejected(self):
        _, token_a = self.request()
        other_email = f"other-{self.token_id}@example.com"
        signup_service.request_signup(**self.payload(email=other_email, business_name="Other"))
        token_b = self.sent_tokens[-1][1]
        with self.assertRaisesRegex(frappe.ValidationError, "invalid"):
            self.verify("wrong-token")
        self.verify(token_a)
        self.assertFalse(frappe.db.exists("User", other_email))
        self.assertTrue(signup_service.inspect_verification_token(token_b)["valid"])

    def test_04_expired_token_is_rejected_without_tenant(self):
        _, token = self.request()
        name = frappe.db.get_value("CreditFlow Pending Signup", {"email": self.email}, "name")
        frappe.db.set_value("CreditFlow Pending Signup", name, "token_expires_at", add_to_date(now_datetime(), minutes=-1))
        with self.assertRaisesRegex(frappe.ValidationError, "expired"):
            self.verify(token)
        self.assertFalse(frappe.db.exists("User", self.email)); self.assertFalse(frappe.db.exists("Business", {"email":self.email}))

    def test_05_consumed_token_replay_is_safe_and_idempotent(self):
        _, token = self.request(); first = self.verify(token); second = self.verify(token)
        self.assertTrue(first["ok"]); self.assertTrue(second["already_verified"])
        pending = frappe.get_doc("CreditFlow Pending Signup", {"email":self.email})
        self.assertEqual(frappe.db.count("Business", {"email":self.email}), 1)
        self.assertEqual(frappe.db.count("CreditFlow Subscription", {"business":pending.business}), 1)

    def test_06_double_verification_creates_one_tenant(self):
        _, token = self.request()
        results = [self.verify(token), self.verify(token)]
        self.assertEqual(sum(not item.get("already_verified") for item in results), 1)
        self.assertEqual(frappe.db.count("User", {"name":self.email}), 1)
        self.assertEqual(frappe.db.count("Business", {"email":self.email}), 1)

    def test_07_resend_rotates_token_and_invalidates_old_token(self):
        _, old = self.request(); pending = frappe.get_doc("CreditFlow Pending Signup", {"email":self.email})
        pending.last_verification_sent_at = add_to_date(now_datetime(), minutes=-2); pending.save(ignore_permissions=True)
        signup_service.resend_signup_verification(self.email); new = self.sent_tokens[-1][1]
        self.assertNotEqual(old, new)
        self.assertFalse(signup_service.inspect_verification_token(old)["valid"])
        self.assertTrue(signup_service.inspect_verification_token(new)["valid"])
        self.verify(new)

    def test_08_resend_cooldown_and_hourly_limit(self):
        self.request(); count = len(self.sent_tokens)
        signup_service.resend_signup_verification(self.email)
        self.assertEqual(len(self.sent_tokens), count)
        pending = frappe.get_doc("CreditFlow Pending Signup", {"email":self.email})
        pending.last_verification_sent_at = add_to_date(now_datetime(), minutes=-2)
        pending.resend_window_start = now_datetime(); pending.resend_count = signup_service.RESEND_RATE_LIMIT
        pending.save(ignore_permissions=True)
        signup_service.resend_signup_verification(self.email)
        self.assertEqual(len(self.sent_tokens), count)

    def test_09_password_is_never_persisted_in_pending_or_business(self):
        _, token = self.request(); pending = frappe.get_doc("CreditFlow Pending Signup", {"email":self.email})
        self.assertNotIn(self.password, frappe.as_json(pending.as_dict()))
        self.verify(token)
        pending.reload()
        for doctype, name in (("CreditFlow Pending Signup",pending.name),("User",self.email),("Business",pending.business)):
            self.assertNotIn(self.password, frappe.as_json(frappe.get_doc(doctype,name).as_dict()))

    def test_10_authority_injection_is_rejected_before_pending_creation(self):
        attacks = {"role":"System Manager", "roles":["Administrator"], "business":"BUS-B", "plan":"PRO",
                   "status":"ACTIVE", "trial_days":365, "subscription":"CFSUB-X", "owner":"Administrator",
                   "redirect":"https://evil.example"}
        for field, value in attacks.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(frappe.ValidationError, "Unsupported"):
                    signup_service.request_signup(**self.payload(**{field:value}))
        self.assertFalse(frappe.db.exists("CreditFlow Pending Signup", {"email":self.email}))

    def test_11_verification_authority_injection_is_rejected(self):
        _, token = self.request()
        for field, value in (("role","System Manager"),("plan","PRO"),("status","ACTIVE"),("trial_days",365),("business","BUS-B")):
            with self.subTest(field=field):
                with self.assertRaisesRegex(frappe.ValidationError, "Unsupported"):
                    signup_service.verify_and_provision(token, self.password, **{field:value})
        self.assertFalse(frappe.db.exists("User", self.email))

    def test_12_smtp_failure_preserves_retry_state_but_creates_no_tenant(self):
        self.mail_patch.stop()
        with patch("creditflow.signup._send_verification_email", side_effect=frappe.OutgoingEmailError("SMTP down")):
            result = signup_service.request_signup(**self.payload())
        self.mail_patch.start()
        self.assertFalse(result["sent"])
        self.assertTrue(frappe.db.exists("CreditFlow Pending Signup", {"email":self.email}))
        self.assertFalse(frappe.db.exists("User", self.email)); self.assertFalse(frappe.db.exists("Business", {"email":self.email}))

    def test_13_public_duplicate_responses_do_not_enumerate_accounts(self):
        first, _ = self.request(); second = signup_service.request_signup(**self.payload())
        unknown = signup_service.resend_signup_verification(f"unknown-{self.token_id}@example.com")
        known = signup_service.resend_signup_verification(self.email)
        self.assertEqual(first["message"], second["message"])
        self.assertEqual(unknown["message"], known["message"])

    def test_14_business_profile_completion_and_routing(self):
        _, token = self.request(); self.verify(token)
        business_name = frappe.db.get_value("User", self.email, "creditflow_business")
        frappe.set_user(self.email)
        self.assertEqual(get_creditflow_home_page(self.email), "creditflow-onboarding")
        result = complete_business_profile(business_name="Legal Name", phone="123", address="Tunis")
        business = frappe.get_doc("Business", business_name)
        self.assertEqual(result["redirect_to"], "/app/creditflow")
        self.assertEqual(business.onboarding_status, "COMPLETED"); self.assertTrue(business.onboarding_completed_at)
        self.assertIsNone(get_creditflow_home_page(self.email))

    def test_15_optional_profile_skip_completes_without_erasing_values(self):
        _, token = self.request(); self.verify(token)
        business_name = frappe.db.get_value("User", self.email, "creditflow_business")
        before = frappe.get_doc("Business", business_name)
        frappe.set_user(self.email); complete_business_profile(skip=1)
        after = frappe.get_doc("Business", business_name)
        self.assertEqual(after.business_name, before.business_name); self.assertEqual(after.phone, before.phone)
        self.assertEqual(after.onboarding_status, "COMPLETED")

    def test_16_staff_cannot_complete_owner_onboarding(self):
        _, token = self.request(); self.verify(token)
        business = frappe.db.get_value("User", self.email, "creditflow_business")
        staff_email = f"staff-{self.token_id}@example.com"
        staff = frappe.get_doc({"doctype":"User","email":staff_email,"first_name":"Staff","send_welcome_email":0,"creditflow_business":business}).insert(ignore_permissions=True)
        staff.add_roles("STAFF"); frappe.set_user(staff_email)
        with self.assertRaises(frappe.PermissionError): complete_business_profile(skip=1)
        self.assertEqual(frappe.db.get_value("Business",business,"onboarding_status"), "PROFILE_PENDING")

    def test_17_owner_cannot_access_or_complete_business_b(self):
        _, token = self.request(); self.verify(token)
        own = frappe.db.get_value("User",self.email,"creditflow_business")
        other = frappe.get_doc({"doctype":"Business","business_name":f"Other {self.token_id}"}).insert(ignore_permissions=True)
        frappe.set_user(self.email)
        self.assertFalse(frappe.get_doc("Business",other.name).has_permission("read"))
        complete_business_profile(skip=1)
        self.assertFalse(frappe.db.get_value("Business",other.name,"onboarding_status"))
        self.assertEqual(frappe.db.get_value("Business",own,"onboarding_status"), "COMPLETED")

    def test_18_existing_accounts_and_businesses_are_unchanged(self):
        legacy = frappe.get_doc({"doctype":"Business","business_name":f"Legacy {self.token_id}"}).insert()
        user = frappe.get_doc({"doctype":"User","email":f"legacy-{self.token_id}@example.com","first_name":"Legacy","send_welcome_email":0}).insert(ignore_permissions=True)
        signup_service.request_signup(**self.payload())
        self.assertFalse(frappe.db.get_value("Business",legacy.name,"onboarding_status"))
        self.assertFalse(frappe.db.get_value("User",user.name,"creditflow_business"))
        self.assertFalse(frappe.db.exists("CreditFlow Subscription", {"business":legacy.name}))

    def test_19_verification_failure_rolls_back_all_tenant_records(self):
        _, token = self.request()
        with patch("creditflow.signup.create_trial_subscription", side_effect=frappe.ValidationError("forced subscription failure")):
            with self.assertRaisesRegex(frappe.ValidationError, "forced subscription failure"):
                self.verify(token)
        self.assertFalse(frappe.db.exists("User",self.email)); self.assertFalse(frappe.db.exists("Business", {"email":self.email}))
        self.assertEqual(frappe.db.get_value("CreditFlow Pending Signup", {"email":self.email}, "status"), "PENDING")

    def test_20_whitelist_surface_contains_no_privileged_internal(self):
        self.assertIn(signup_service.signup, frappe.whitelisted)
        self.assertIn(signup_service.resend_verification, frappe.whitelisted)
        self.assertIn(signup_service.complete_verification, frappe.whitelisted)
        for fn in (signup_service.create_self_service_tenant, signup_service.request_signup,
                   signup_service.verify_and_provision, signup_service._create_business, signup_service._assign_owner):
            self.assertNotIn(fn, frappe.whitelisted)
