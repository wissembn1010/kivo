"""One-off validation on the throwaway CI site, using the real WSGI application."""
import os
from contextlib import nullcontext
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

import requests
import frappe
from frappe.app import application
from frappe.utils.jinja import get_jenv
from werkzeug.serving import make_server

from creditflow.i18n import get_language_context
from creditflow.tests.test_onboarding_website import IntegrationTestOnboardingWebsite


site = "test_kivo.localhost"
os.chdir(Path("/home/frappe/frappe-bench/sites"))
frappe.init(site=site, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
assert "creditflow.website.update_website_context" in frappe.get_hooks("update_website_context")
for restricted in (False, True):
    env = get_jenv(restrict_globals=restricted)
    assert env.globals["get_language_context"] is get_language_context
    assert env.from_string("{{ get_language_context('ar-TN').kivo_direction }}").render() == "rtl"
    print(f"PASS actual Frappe Jinja registration and render, restrict_globals={restricted}", flush=True)

fixture = IntegrationTestOnboardingWebsite("test_owner_get_renders_own_business_context")
fixture.token = uuid4().hex[:12]
business = fixture.create_business("HTTP validation", phone="111111", address="Fixture address")
owner = fixture.create_owner("http", business.name)
key, secret = uuid4().hex, uuid4().hex
user = frappe.get_doc("User", owner)
user.api_key, user.api_secret = key, secret
user.save()
frappe.db.commit()
headers = {"X-Frappe-Site-Name": site, "Host": site, "Authorization": f"token {key}:{secret}"}
server = make_server("127.0.0.1", 0, application, threaded=True)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
url = f"http://127.0.0.1:{server.server_port}"
with requests.Session() as session:
    session.headers.update(headers)
    try:
        for no_hook in (False, True):
            hook = patch("creditflow.website.update_website_context", return_value=None) if no_hook else nullcontext()
            with hook as observed:
                for language, direction in (("en", "ltr"), ("fr", "ltr"), ("ar", "rtl"), ("ar-TN", "rtl")):
                    change = session.post(url + "/api/method/creditflow.i18n.set_language",
                                          json={"language": language}, timeout=30)
                    assert change.status_code == 200, change.status_code
                    assert change.json()["message"] == {"language": language, "direction": direction}
                    assert change.cookies.get("preferred_language") == language
                    response = session.get(url + "/creditflow-onboarding", params={"_lang": language},
                                           allow_redirects=False, timeout=30)
                    assert response.status_code == 200, response.status_code
                    for expected in (f'<option value="{language}" selected>', f'const direction = "{direction}";',
                                     'id="cf-profile-form"', business.business_name, business.phone, business.address):
                        assert expected in response.text, expected
                    print(f"PASS HTTP {response.status_code} {language} {direction}, website_hook_disabled={no_hook}", flush=True)
                if no_hook:
                    assert observed.call_count >= 4
        frappe.db.rollback()
        business.reload()
        assert business.onboarding_status == "PROFILE_PENDING"
        assert frappe.db.get_value("User", owner, "language") == "ar-TN"
        response = session.post(url + "/api/method/creditflow.saas_onboarding.complete_business_profile",
                                json={"business_name": business.business_name, "phone": "222222",
                                      "address": business.address}, timeout=30)
        assert response.status_code == 200, response.status_code
        assert response.json()["message"] == {"ok": True, "redirect_to": "/desk/kivo"}
        frappe.db.rollback()
        business.reload()
        assert business.onboarding_status == "COMPLETED" and business.phone == "222222"
        print("PASS HTTP onboarding submission and persisted fixture values")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        frappe.destroy()
