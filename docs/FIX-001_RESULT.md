# FIX-001 RESULT

**Status: PARTIAL. Is FIX-001 closed? NO.**

The original B-28 Business-field takeover is blocked. All 19 stored-Business regression tests pass. A separate confirmed child-parent takeover remains open, so the remediation plan's forged-child/parent acceptance criterion is not met. See [the separate P0-equivalent finding](FIX-001_NEW_FINDINGS.md). FIX-002 and all other remediation implementations were left untouched.

## Scope and database safety

Work was limited to the shared tenant authorization implementation, FIX-001 regression tests, and this evidence/report. No schema or migration change, no deployment configuration change, no production/customer record mutation, and no automatic reassignment of existing Business values occurred. All database execution used `kivo-fix001-tests.localhost`, with synthetic records; the separate child probe explicitly rolled back. The existing full test suite includes destructive migration tests and was run only on this dedicated test site.

The workspace already contained unrelated edits to hooks, signup, website, permission-hardening/signup/branding tests, and the verified-initial-owner portion of `permissions.py`, plus an untracked draft of the stored-Business regression test. Those unrelated edits were preserved. They are not claimed as part of this fix. The regression draft was reproduced and extended.

## Root cause and exact mutation path

1. `POST /api/method/frappe.client.bulk_update` invokes `frappe/client.py::bulk_update`.
2. It loads the known foreign document with `frappe.get_doc(doctype, docname)` without a tenant permission precheck.
3. `existing_doc.update(incoming_fields)` replaces `business` with the attacker's Business.
4. `Document._save` calls `check_permission("write")` against that mutated document.
5. Kivo's `has_permission` compared only `doc.business` with the user's assigned Business. `set_and_validate_business`, registered on `before_validate`, made the same comparison.
6. Frappe subsequently locks/loads the original row in `check_if_latest`, but uses it for timestamp/docstatus validation, not original-tenant authorization.

Thus the trusted User membership lookup was compared to untrusted document ownership. No direct request-argument comparison was necessary: request arguments had already become document values. List `permission_query_conditions` did not apply to the known-ID mutation. Hidden fields, browser restrictions, and the Desk UI were not an effective boundary.

`frappe.client.set_value`, `frappe.client.save`, Desk `savedocs`, REST v1/v2 updates, and direct `Document.save` also reach document save with supplied/mutated ownership. The correction belongs in Kivo's shared hooks, not a single endpoint override.

## Implementation

Changes attributable to this task:

| File | Change |
| --- | --- |
| `creditflow/permissions.py` | 27 added lines: shared persisted-ownership helper and two checks |
| `creditflow/tests/test_stored_business_authorization.py` | Extended the existing draft to 19 regression tests and a repeatable in-memory probe |
| `docs/FIX-001_RESULT.md` | Findings, scope, test evidence, and exact outstanding failures |
| `docs/FIX-001_NEW_FINDINGS.md` | Separate confirmed P0-equivalent child-parent bypass |

Functions changed: `has_permission`, `set_and_validate_business`. Function added: `_has_stored_business_access`.

For a named existing record, the helper queries the database directly with `cache=False`, using `name` for Business and `business` for other registered tenant DocTypes. The persisted owner must equal the user's assigned Business, and the incoming owner must equal the persisted owner. An existing row with a null Business is denied; it is not mistaken for a missing/new row. The lookup ignores attacker-controlled `__islocal` and original-document snapshots.

Mutation permission checks use `SELECT ... FOR UPDATE` through `get_value(for_update=True)`, retaining the ownership lock through save. `before_validate` repeats the uncached locking check, also covering existing trusted generated saves that skip the normal role permission check. Normal Frappe checks still govern roles, timestamps, links, docstatus, and domain validation.

For genuinely new rows, the existing assigned-Business check and safe defaulting remain. Administrator and the existing explicitly privileged System Manager role retain cross-Business authority. No new `ignore_permissions=True` implementation, role, flag, or request-controlled bypass was added.

No tenant-level workflow requiring an existing record's Business reassignment was found. Onboarding resolves the user's Business and keys existing batches by Business; generated economic records inherit the authorized source Business. Verified signup creates a new Business and initial user assignment. Existing non-tenant provisioning/system behavior was preserved, and signup, onboarding, generated ledgers, and subscription tests pass. Migration/fixture implementations were not changed.

## Pattern search before implementation

Inspected the shared hooks, DocType schemas/role permissions, all listed controllers, onboarding/signup services, subscription/billing services, and patches.

| Records | Previous pattern / reachability |
| --- | --- |
| Customer, Product, Supplier | Same incoming-Business pattern; actual B→A saves reproduced through bulk update. OWNER can write; STAFF master restrictions remain. |
| Sale, Purchase, Payment, Supplier Payment | Same hooks; linked Customer/Supplier/Product checks compare against incoming Business. Retargeting links can satisfy those checks. An actual foreign Sale draft takeover by A STAFF was reproduced. |
| Credit Transaction, Supplier Transaction, Stock Movement | Same hooks. Source-integrity, role, and submitted-document checks are additional constraints, not authorization of persisted Business. |
| Sale Return, Sale Reversal, Payment Reversal, Purchase Reversal, Supplier Payment Reversal | Same hooks. Original-source Business comparisons use incoming Business; source/status constraints alone do not establish ownership of the draft being edited. |
| Onboarding Import Batch | Same hooks, but ordinary OWNER has read-only DocType permission; controlled imports use tenant-scoped keys. |
| CreditFlow Subscription, CreditFlow Billing Payment, Withholding Tax | Same hooks; ordinary users lack generic write permission. Internal operations remain subject to their existing service boundaries. |
| TEJ Export Batch | Same tenant hooks; OWNER has mutation permission. Included in the persisted-ownership hook regression. |
| Business | Tenant identity is `name`, not a mutable `business` field. Existing cross-Business denial and own-Business operations preserved. |
| CreditFlow Pending Signup | No ordinary tenant CRUD access; System Manager read permission only, controlled signup service. Its Business link is assigned during provisioning; it is outside the generic tenant hook mapping. |
| CreditFlow Plan / entitlements | Shared platform records, not tenant-owned Business records. No changes. |
| User | Separate `creditflow_business` membership gate, Frappe User permissions, and verified-signup capability. No additional User takeover was confirmed; no changes. |
| Child tables | Separate incoming-parent authorization path. Sale Item takeover confirmed and recorded separately; no child-controller changes. |

The shared correction protects the existing 21 registered tenant DocTypes without editing their controllers, schemas, or role permissions. A regression exercises persisted ownership denial through both hooks and actual bulk update for every registered DocType. This is not a claim that every domain workflow or every child table has been independently proven secure.

## Reproduction and regression evidence

Before changing implementation:

- Initial regression draft: **13 tests; 10 failing assertions/subtests**. Customer, Product, Supplier, and Sale takeover paths succeeded.
- Expanded pre-fix suite: **16 tests; 11 failures, 1 error**. The error was indexing the expected bulk failure entry when the vulnerable HTTP request returned an empty `failed_docs`; that assertion was subsequently made explicit.
- Actual in-memory `bulk_update` + Kivo-hook probe: **BEFORE: VULNERABLE**.

After implementation:

- Final stored-Business suite: **19 tests, OK**.
- Exact same in-memory probe: **AFTER: BLOCKED**.
- HTTP tests construct Werkzeug requests and exercise Frappe's real API router and whitelist dispatcher inside the test database transaction. They test server routing and mutation permissions with an established test user; they are not network/login/session-authentication tests.

| Attack / operation | Result |
| --- | --- |
| A: A OWNER supplies business=A to update B Customer | DENIED; stored Business, timestamp, and notes unchanged |
| B: Ordinary edit of B Customer | DENIED |
| C: A Customer create (including default Business), read, ordinary edit, bulk edit | ALLOWED |
| D: A Customer → B Business | DENIED |
| E: Direct save / bulk / client API / Desk / REST v1/v2 reassignment | DENIED |
| B Customer read / delete | DENIED |
| New Customer assigned directly to B | DENIED |
| Product / Supplier takeover via bulk, client save, Desk savedocs | DENIED |
| Foreign draft Sale retargeting, including replacement child rows | DENIED; no stock/credit events created |
| Forged new flag / original snapshot / missing stored Business | DENIED |
| Validation-time ownership change; save skipping role checks | DENIED |
| Administrator / System Manager explicit reassignment | ALLOWED |
| Separate foreign Sale Item incoming-parent takeover | VULNERABLE; separately recorded, not fixed |

Regression coverage also includes hook denial for every tenant DocType. The additional validation test injects an ownership change after the early permission check and proves the validation-time database recheck; it is not a concurrent multi-connection stress test.

Repeatable commands:

```sh
bench --site kivo-fix001-tests.localhost execute creditflow.tests.test_stored_business_authorization.run_in_memory_probe
bench --site kivo-fix001-tests.localhost run-tests --app creditflow --module creditflow.tests.test_stored_business_authorization
bench --site kivo-fix001-tests.localhost run-tests --app creditflow
```

## Full existing suite and unresolved baseline failures

Before: **383 tests, 15 failures, 5 errors** (includes the original failing regression draft).

After: **389 tests, 379 passed, 5 failures, 5 errors**. All ten remaining failures/errors were present before the implementation change. No new full-suite failure was introduced. The full run includes the 19 FIX-001 tests; the final focused run additionally checks the last added REST v2 and Product/Supplier save assertions.

Relevant passing groups include: Business isolation (6), adversarial Business isolation (7), permission hardening (10), Customer payment workflow (11), sales/Customer regression (10), stock movement (6), Purchase (8), Purchase reversal (9), purchase/Supplier regression (15), supplier debt (10), purchase payment modes (11), verified signup (25), subscription foundation (11), and onboarding import/website/migration groups (6/7/21). Report/API isolation checks pass. The Customer DocType's own test file is an empty template; the executable Customer coverage is in the workflow/security modules. Sale Return has eight passing tests and the pre-existing print failure below.

Exact unresolved failures:

| Module / test | Failure observed both before and after |
| --- | --- |
| `test_sale_returns.test_duplicate_posting_and_print` | `AssertionError: '119.000' not found` in rendered print HTML |
| `test_sale_fiscal_invoice.test_invoice_print_contains_snapshotted_fiscal_data` | `AssertionError: '180.000' not found` in rendered print HTML (amount rendered with two decimals) |
| `test_language_navigation.test_authenticated_home_and_back_links_return_to_kivo` | `AssertionError: None != 'en'` |
| `test_kivo_branding_navigation.test_authenticated_root_loads_kivo_desk` | `AssertionError: None is not true`; hard-coded expected user is absent on the dedicated test site |
| `test_kivo_branding_navigation.test_owner_login_response_targets_kivo` | `AssertionError: '/desk/kivo' != '/app/kivo'` |

Five `test_withholding_tej` errors share the exact exception `frappe.exceptions.ValidationError: A valid official TEJ Operation Code is required for withholding.`:

- `test_01_decimal_calculation_and_millimes`
- `test_02_full_partial_and_ordinary_supplier_balance`
- `test_03_reversal_restores_debt_and_preserves_fiscal_history`
- `test_05_official_xsd_export_and_invalid_identity`
- `test_06_tenant_known_id_access_and_cross_batch_link_denied`

No reference data, formatting configuration, routing, or unrelated test assertions were changed to hide these failures. Ruff lint passes on the implementation/test files, and the regression file passes Ruff formatting.

Raw logs: `/tmp/kivo_fix001_before.log`, `/tmp/kivo_fix001_expanded_before.log`, `/tmp/kivo_fix001_baseline_full.log`, `/tmp/kivo_fix001_final_focus.log`, `/tmp/kivo_fix001_after_full.log`, `/tmp/kivo_fix001_child_probe.log`.

**Database migration required:** NO. **Existing production/customer data modified:** NO. Synthetic test data only.

**Regression risk:** MEDIUM. The patch is small and tested, but it touches shared tenant authorization and adds uncached ownership reads/row locks. Dedicated-test-site baseline failures limit a completely green validation claim. The separately open child-parent vulnerability is a security gap, not a regression introduced by this patch.

**Is FIX-001 closed? NO.** The original Business-field exploit is blocked; the separately recorded child-parent bypass prevents full closure. No other remediation implementation was started.
