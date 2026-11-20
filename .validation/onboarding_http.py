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


def browser_switches(origin, owner, secret):
    import time
    from urllib.parse import urlsplit
    from frappe.utils.password import update_password

    update_password(owner, secret)
    frappe.db.commit()
    login = requests.post(origin + "/api/method/login", data={"usr": owner, "pwd": secret},
                          headers={"Host": site}, timeout=30)
    assert login.status_code == 200 and login.cookies.get("sid")
    driver = "http://127.0.0.1:9515/session"
    result = requests.post(driver, json={"capabilities": {"alwaysMatch": {
        "browserName": "chrome", "goog:chromeOptions": {"args": [
            "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
            "--host-resolver-rules=MAP test_kivo.localhost 127.0.0.1",
        ]}}}}, timeout=45)
    result.raise_for_status()
    driver += "/" + result.json()["value"]["sessionId"]

    def command(path, data):
        response = requests.post(driver + path, json=data, timeout=35)
        response.raise_for_status()
        return response.json()["value"]

    base = f"http://{site}:{urlsplit(origin).port}"
    try:
        command("/url", {"url": base + "/api/method/ping"})
        command("/cookie", {"cookie": {"name": "sid", "value": login.cookies["sid"], "path": "/"}})
        command("/url", {"url": base + "/creditflow-onboarding"})
        for lang, direction in (("fr", "ltr"), ("ar", "rtl"), ("ar-TN", "rtl"), ("en", "ltr")):
            option = command("/element", {"using": "css selector",
                "value": f'#kivo-language-select option[value="{lang}"]'})
            command("/element/" + option["element-6066-11e4-a52e-4f735466cecf"] + "/click", {})
            expected = [lang, direction, lang, lang, True]
            for _ in range(100):
                state = command("/execute/sync", {"script": """
                    return [document.documentElement.lang, document.documentElement.dir,
                        document.querySelector('#kivo-language-select')?.value,
                        new URL(location.href).searchParams.get('_lang'),
                        !!document.querySelector('#cf-profile-form')];""", "args": []})
                if state == expected:
                    break
                time.sleep(0.1)
            assert state == expected, (state, expected)
            frappe.db.rollback()
            assert frappe.db.get_value("User", owner, "language") == lang, lang
            print(f"PASS Chrome selector click, navigation, DOM and saved preference: {lang} {direction}", flush=True)
    finally:
        requests.delete(driver, timeout=20)

site = "test_kivo.localhost"
os.chdir(Path("/home/frappe/frappe-bench/sites"))
frappe.init(site=site, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
assert "creditflow.website.update_website_context" in frappe.get_hooks("update_website_context")
baseline = os.environ.get('BASELINE_CONTROL') == '1'
for restricted in (() if baseline else (False, True)):
    env = get_jenv(restrict_globals=restricted)
    assert env.globals["get_language_context"] is get_language_context
    assert env.from_string("{{ get_language_context('ar-TN').kivo_direction }}").render() == "rtl"
    selector = env.get_template("creditflow/templates/includes/kivo_language_selector.html")
    rendered = selector.render(kivo_language="ar-TN")
    assert '<option value="ar-TN" selected>' in rendered and 'const direction = "rtl";' in rendered
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
from werkzeug.middleware.shared_data import SharedDataMiddleware
web_app = SharedDataMiddleware(application, {"/assets": str(Path("assets").resolve())})
server = make_server("127.0.0.1", 0, web_app, threaded=True)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
url = f"http://127.0.0.1:{server.server_port}"
with requests.Session() as session:
    session.headers.update(headers)
    try:
        if baseline:
            from frappe.utils import jinja
            from jinja2 import UndefinedError

            response = session.get(url + "/creditflow-onboarding", timeout=30)
            assert response.status_code == 200 and 'id="cf-profile-form"' in response.text
            print("BASELINE normal hook: HTTP 200, onboarding form present", flush=True)
            with patch("creditflow.website.update_website_context", return_value=None) as hook:
                with patch.object(jinja, "_throw_render_error", wraps=jinja._throw_render_error) as errors:
                    response = session.get(url + "/creditflow-onboarding", timeout=30)
                    assert hook.called and response.status_code == 500, response.status_code
                    exceptions = [call.kwargs.get("exc") for call in errors.call_args_list]
                    assert any(isinstance(e, UndefinedError) and str(e) == "'kivo_languages' is undefined"
                               for e in exceptions), [type(e).__name__ for e in exceptions]
            print("BASELINE missing hook: HTTP 500, 'kivo_languages' is undefined", flush=True)
            raise SystemExit(0)
        if os.environ.get("BROWSER_CONTROL") == "1":
            browser_switches(url, owner, secret)
            raise SystemExit(0)
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
