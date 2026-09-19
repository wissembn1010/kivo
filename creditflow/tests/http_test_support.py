"""Run the real Frappe WSGI application in isolated request contexts."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import frappe


def request(path, *, method="POST", payload=None, headers=None, remote_addr="192.0.2.200"):
    return request_many([dict(path=path, method=method, payload=payload,
        headers=headers, remote_addr=remote_addr)])[0]


def request_many(calls):
    site, sites_path = frappe.local.site, frappe.local.sites_path

    def run(call):
        from frappe.utils import get_test_client

        client = get_test_client(use_cookies=False)
        response = client.open(
            call["path"], method=call.get("method", "POST"), json=call.get("payload"),
            base_url=f"https://{site}",
            headers={"X-Frappe-Site-Name": site, **(call.get("headers") or {})},
            environ_overrides={"REMOTE_ADDR": call.get("remote_addr", "192.0.2.200")},
        )
        try:
            return response.status_code, response.get_json()
        finally:
            response.close()

    with patch("frappe.app._sites_path", sites_path), ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(run, call) for call in calls]
        return [future.result(timeout=45) for future in futures]
