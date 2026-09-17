ID: B-02

Status: PASS — CLOSED.

Root cause: The Sale form multiplied quantity by untaxed unit price and used that HT total for CASH payment and MIXED outstanding amounts, including on submitted refresh. Pricing/preview work was not awaited before validation, and stale Product replies could overwrite newer inputs. Draft previews now reuse Sale.validate_items_and_calculate_total through a read-only, create-permission-checked endpoint. Customer/Product ownership is checked against the authorized Business; incoming identities, parent links, flags and fiscal snapshots are excluded. Submission retains all existing payment, pricing, credit, stock and tenant validations. Request/input guards discard stale previews; validation waits for pricing edits; submitted forms do not recalculate.

Files changed (relative to apps/creditflow):

- creditflow/public/js/business_defaults.js — Sale-only preview/payment and async guards; Purchase calculation unchanged.
- creditflow/creditflow/doctype/sale/sale.py — preview_totals; existing fiscal/submission methods unchanged.
- creditflow/tests/test_sale_form.cjs — browser-script regression harness.
- creditflow/tests/test_sale_fiscal_invoice.py — three integration regressions.
- docs/B-02_RESULT.md — this report.

Tests added: Nine Node tests execute the actual form script: CASH119/MIXED69; submitted refresh immutability; out-of-order quantities; awaited validation; stale Product response; pending pricing before save; discounted TTC; failed/late preview; nested pricing/payment field events. Three Frappe tests cover preview → submit → reload, mixed TVA/discount and legacy Decimal rounding without preview writes, and STAFF same-tenant access versus foreign Business/Customer/Product and Guest denial. Before correction, all five initial JavaScript tests failed (including 100 != 119 and submitted snapshot mutation); the three server tests failed because the endpoint did not exist. New test-fixture corrections during implementation: initialize the zero outstanding default and use the existing discounted-unit rounding rule rather than a mistaken expected value. No existing baseline assertion was changed.

Focused tests: 12/12 passed — Node 9/9; Frappe 3/3. Commands: node --test apps/creditflow/creditflow/tests/test_sale_form.cjs; bench --site kivo-fix001-tests.localhost run-tests --app creditflow --module creditflow.tests.test_sale_fiscal_invoice --test test_form_preview_matches_submit_and_reload --test test_form_preview_discount_rounding_and_no_writes --test test_form_preview_enforces_role_and_linked_tenants. Server logs: /tmp/kivo_b02_before.log and /tmp/kivo_b02_focus.log. JavaScript results are in the execution transcript. Syntax check and git diff --check passed.

Related tests: 92 total / 91 passed / 1 documented baseline failure. Fiscal Invoice 11/12 (existing print assertion missing 180.000), sales/customer 10/10, purchase/supplier 15/15, UOM 11/11, stored Business authorization 19/19, child authorization 18/18, shared UOM 7/7. Log: /tmp/kivo_b02_related.log. Existing FIX-001 and B-04 protections remain intact. Immediately related paths checked: Sale hooks registration, form refresh/validation/pricing/payment handlers, Sale fiscal/payment methods, and the existing create/tenant permission hook. No broad audit.

Full suite: bench --site kivo-fix001-tests.localhost run-tests --app creditflow — 425 total / 415 passed / 5 failures / 5 errors, 90.588 seconds, exit 1. Log: /tmp/kivo_b02_full.log. Compared directly with documented B-04 baseline /tmp/kivo_b04_full.log: 422 total / 412 passed / 5 failures / 5 errors. Three additional integration passes; Node tests are separate from Frappe totals.

New regressions: NONE. Exact failure identities and causes match B-04: Sale Return print missing 119.000; Fiscal Invoice print missing 180.000; language None != en; branding root None is not true; branding login /desk/kivo != /app/kivo. Same five TEJ errors (tests 01/02/03/05/06): valid official TEJ Operation Code required. These remain unresolved, not reclassified or waived.

Migration required: NO.

Data modified: Synthetic dedicated-test-site fixtures only. NO production/customer data, staging configuration, ledger repairs or historical fiscal snapshots modified. Preview tests confirm unchanged Sale/Sale Item/Stock Movement/Credit Transaction counts.

Remaining issue: None for B-02 acceptance under the tested script/server paths. JavaScript verification uses the actual script with mocked Frappe transport/field events; this is not a deployed-browser certification. Deployment/assets and separately tracked concurrency/print issues are not claimed closed. kivo-fix/test/security guided the scoped correction and authorization coverage; no debug skill or unrelated remediation was needed.

Remaining Class A count: 7, accounting for B-14, B-04 and B-02 closure. No next blocker started.
