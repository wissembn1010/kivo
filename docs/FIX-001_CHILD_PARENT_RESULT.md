# FIX-001 CHILD AUTHORIZATION RESULT

**Status: PASS** (security regression criteria; the full suite retains its documented unrelated baseline failures).

**Original child exploit before:** `BEFORE: VULNERABLE`.

**Original child exploit after:** `AFTER: BLOCKED`.

**Original stored-Business exploit:** `BLOCKED`; all existing 19 FIX-001 tests pass. `permissions.py` was not changed during this child-authorization follow-up.

## Root cause

Frappe's `has_child_permission` authorizes the `parent`, `parenttype`, and `parentfield` of an incoming child Document. `frappe.client.bulk_update` changes those fields before `save()`. An A STAFF user could therefore supply an A Sale as the parent of a persisted B Sale Item and change the foreign row. Frappe's timestamp check loads the original child but does not authorize its original parent.

Parent saves introduce another path: Frappe normalizes incoming child parent fields, then persists children with `db_update`, without running their `validate` hooks. Supplying a foreign child ID inside an authorized parent could overwrite that row. A child `before_validate` hook alone would not protect this path. Frappe also handles child permissions before app `has_permission` hooks, so registering the existing Business hook on child DocTypes would not solve it.

## Files and functions changed

- `creditflow/child_permissions.py` (new): `authorize_child`, `_authorize_parent`, `_relationship`, `_deny`, `validate_child_rows`, `validate_child_change`; `ChildAuthorizationMixin.has_permission`, `.db_insert`, `.db_update`.
- `creditflow/hooks.py`: register the child class extension, child `before_change`/`on_trash` events, and parent `before_validate`/`before_update_after_submit` guards.
- `creditflow/tests/test_child_parent_authorization.py` (new): 18 security/integration tests, using synthetic fixtures only.
- `docs/FIX-001_CHILD_PARENT_RESULT.md` (new): this report.
- `docs/FIX-001_RESULT.md` and `docs/FIX-001_NEW_FINDINGS.md`: add a follow-up closure link while preserving the historical partial result and original reproduction evidence.

Unrelated workspace changes predating this task were preserved. No Frappe core patch, schema change, UI change, pricing change, or FIX-002 work was included.

## Child inventory and vulnerability matrix

The source inventory covers every Kivo `istable` schema and its actual Table field. A regression checks that the inventory stays complete. There is no Purchase Return Item, separate invoice-item table, payment-child table, or stock-movement-child table in this repository. Fiscal invoices use Sale Item. Import input rows are not a separate persisted Table DocType; the persisted import result table is included below. Subscription entitlements belong to the shared plan.

| Child DocType | Parent / parentfield | Tenant-owned parent? | Direct server mutation possible? | Before | Protected after? |
| --- | --- | --- | --- | --- | --- |
| Sale Item | Sale / items | Yes | STAFF/OWNER bulk, save, REST, parent save, document persistence | Confirmed bulk takeover and embedded foreign-ID takeover | Yes |
| Purchase Item | Purchase / items | Yes | STAFF/OWNER bulk, save, REST, parent save, document persistence | Same incoming-parent flaw; valid-row comparison probe confirms takeover | Yes |
| Sale Return Item | Sale Return / items | Yes | OWNER bulk, save, REST, parent save, document persistence | Same flaw; valid-row comparison probe confirms takeover | Yes |
| Product UOM Conversion | Product / uom_conversions | Yes | OWNER bulk, save, REST, parent save, document persistence | Same flaw; valid-row comparison probe confirms takeover | Yes |
| TEJ Export Batch Item | TEJ Export Batch / records | Yes | OWNER bulk, save, REST, parent save, document persistence | Same flaw; valid-row comparison probe confirms takeover | Yes |
| Onboarding Import Result | Onboarding Import Batch / created_records | Yes | Controlled import saves and server document persistence; ordinary API mutation denied by existing parent roles | Generic API was role-protected; raw Document persistence had no tenant guard | Yes; controlled imports pass |
| CreditFlow Plan Entitlement | CreditFlow Plan / entitlements | No; shared platform plan | Administrator/System Manager writes; OWNER read; server document persistence | Generic OWNER API writes were role-protected; raw Document persistence had no guard | Yes; OWNER read preserved, ordinary writes denied |

The additional valid-row comparison probe temporarily disables **only the new authorization helper in memory**, then executes the actual endpoint/persistence code on synthetic rows and rolls back to a savepoint before testing the patched denial. This distinguishes demonstrated generic API takeover from trusted low-level persistence behavior. The initial exact Sale Item reproduction was run against the unmodified implementation before the guard was added.

## Shared authorization mechanism

1. Read the existing child's `parent`, `parenttype`, and `parentfield` directly from the database with `cache=False`, regardless of incoming `__islocal`, snapshots, flags, or `parent_doc`.
2. Validate the relationship against the explicit child-to-parent/field mapping and the actual parent Table metadata. Reject missing parents, unexpected parent types, wrong fields, and invalid table options.
3. Authorize the trusted persisted parent's Business against the user's current legitimate Business. For standalone Document/API permission checks, also require the appropriate parent role permission.
4. For a requested relationship change, validate/authorize the destination too. Ordinary users cannot change any part of an existing child's relationship, including moves between parents in the same Business. No supported Kivo reparenting workflow was found. Normal adding/removing rows through an authorized parent remains supported.
5. Administrator/System Manager retain explicit platform authority for valid relationships, including administrative moves; invalid relationship metadata is rejected even for them.
6. Parent save guards reject embedded foreign IDs before any parent/table write. Child persistence guards cover Frappe's direct `db_insert`/`db_update` calls and their `db_update_all` callers. `before_change` guards child `db_set`; `on_trash` reinforces deletion.
7. New rows require a legitimate destination. During a new parent's validation, the server-owned parent save context is accepted only when no parent row exists and its identity/type match; persistence checks use the parent row once it has been inserted.

Parent saves retain their normal Frappe role authorization, including existing controlled imports. The low-level persistence layer independently enforces tenancy and relationship integrity without treating `ignore_permissions` as a tenant bypass. It does not turn Frappe's trusted server primitives into a new role-permission API. The seven child controllers and their domain behavior were not rewritten.

### Locking and concurrency

Read checks do not acquire row locks. Mutation checks first read the current relationship, then lock/authorize the parent and acquire an uncached child row lock. They re-read and compare the child relationship under that lock; changes since the initial read are denied. Locks remain held through the transaction.

Parent locking uses `wait=False`: parent saves normally lock parent then children, but REST/direct deletion may already hold the child lock before authorization. A conflicting parent lock therefore fails promptly instead of creating a parent/child wait cycle. Such conflicts require retry; they do not grant access. Ordinary reparenting is rejected, avoiding an ongoing multi-parent move workflow. The regression injects a relationship change between initial lookup and locked recheck and verifies denial. A multi-connection stress/load test was not performed.

## Server path review

| Path | Authorization enforcement |
| --- | --- |
| `frappe.client.bulk_update` | Child `has_permission` authorizes persisted parent before save; persistence guard rechecks |
| `frappe.client.save`, child `Document.save` | Same class-level permission and persistence guard; request flags cannot bypass the tenant guard |
| REST v1 PUT / v2 PATCH/PUT | Same Document guard after generic API loads/applies fields |
| `frappe.client.set_value` on a child | Loads the persisted parent, modifies its row, saves parent; parent embedded-row guard and child persistence guard |
| Desk/REST save of a parent containing child IDs | Parent guard before database writes; child `db_update` guard also applies |
| `frappe.client.insert` for a child | Appends to destination parent and saves it; new-child/destination checks apply |
| Parent row removal / client child delete / resource delete | Client resolves persisted parent and checks parent write permission; parent save removes the row |
| `frappe.delete_doc`, `Document.delete`, list-of-names deletion | Reloads the stored document, checks permission, and runs `on_trash`; forging the in-memory parent does not select an authorized replacement |
| Child `db_insert`, `db_update`, `db_update_all` | Extension guard enforces tenant/relationship integrity even though Frappe normally skips validation |
| Child `db_set` | `before_change` executes after incoming values are applied and before database update; checks persisted ownership |
| Child read/get/list/value APIs | Document checks use persisted parent; current Frappe query builder joins parent permissions for child lists. Synthetic own/foreign list regression passes; no list-query patch was needed |
| Kivo JavaScript | Edits rows/forms and saves parents. No client-side restriction is used as authorization |
| Kivo onboarding/imports | Controlled parent saves/persistence; existing onboarding tests pass |
| Custom whitelisted Kivo methods | Source search found no separate generic child-SQL mutation endpoint; reviewed onboarding, UOM, TEJ and controller usage |
| Raw `frappe.db.set_value`, `frappe.db.delete`, SQL | Trusted server database primitives: no Document hooks and not remotely whitelisted. They were not monkey-patched. Audited Kivo calls do not expose attacker-selected child writes through them. Arbitrary privileged Python/SQL execution is outside the tenant API boundary |

## Regression and verification results

Before implementation: **11 tests, 15 failing assertions/subtests**. The exact STAFF Sale Item bulk takeover succeeded with `failed_docs=[]`; a foreign embedded child ID could also be overwritten. Test-harness issues found in the first draft were corrected before this recorded run.

After implementation: **18 new child tests, all pass**. Coverage includes:

- Exact original STAFF takeover; ordinary foreign-field changes; all parent metadata fields.
- B→A and A→B moves, and unsupported same-Business reparenting.
- Client save/set_value, REST v1/v2 updates and deletes, direct Document operations, bulk updates.
- Foreign reads, direct/parent-based deletion, forged-parent-then-delete, list-of-names deletion.
- Embedded foreign child IDs; forged new flags and snapshots; locked relationship recheck.
- Same-tenant editing, new children, removal, and bulk editing.
- Cross-tenant creation and invalid/missing destination metadata.
- Every child table, valid-row comparison probes, admin operations, and shared-plan read/write boundaries.

**Existing FIX-001 tests:** 19/19 pass. The original `run_in_memory_probe` reports `BLOCKED`.

**Full suite result:** Final run pending at report preparation; updated below after completion.

### Existing baseline failures

The earlier 389-test baseline had 379 passes, 5 failures and 5 errors. The first full child-patch run (400 tests, before the final seven additional tests) retained exactly the same five failing tests and five errors, with no new failure.

Five failures:

- `test_sale_returns.test_duplicate_posting_and_print`: `'119.000'` not found in rendered HTML.
- `test_sale_fiscal_invoice.test_invoice_print_contains_snapshotted_fiscal_data`: `'180.000'` not found in rendered HTML.
- `test_language_navigation.test_authenticated_home_and_back_links_return_to_kivo`: `None != 'en'`.
- `test_kivo_branding_navigation.test_authenticated_root_loads_kivo_desk`: `None is not true`; expected hard-coded user absent on test site.
- `test_kivo_branding_navigation.test_owner_login_response_targets_kivo`: `'/desk/kivo' != '/app/kivo'`.

Five `test_withholding_tej` errors, all `frappe.exceptions.ValidationError: A valid official TEJ Operation Code is required for withholding.`:

- `test_01_decimal_calculation_and_millimes`
- `test_02_full_partial_and_ordinary_supplier_balance`
- `test_03_reversal_restores_debt_and_preserves_fiscal_history`
- `test_05_official_xsd_export_and_invalid_identity`
- `test_06_tenant_known_id_access_and_cross_batch_link_denied`

No unrelated assertions, fixtures, settings, or reference data were changed to mask these failures.

Repeatable commands (dedicated test site only):

```sh
bench --site kivo-fix001-tests.localhost run-tests --app creditflow --module creditflow.tests.test_child_parent_authorization
bench --site kivo-fix001-tests.localhost run-tests --app creditflow --module creditflow.tests.test_stored_business_authorization
bench --site kivo-fix001-tests.localhost execute creditflow.tests.test_stored_business_authorization.run_in_memory_probe
bench --site kivo-fix001-tests.localhost run-tests --app creditflow
```

Logs: `/tmp/kivo_child_before.log`, `/tmp/kivo_child_final_focus.log`, `/tmp/kivo_child_stored_business.log`, `/tmp/kivo_child_full_first.log`, `/tmp/kivo_child_full_final.log`. Ruff lint and diff whitespace checks pass.

**Database migration required:** NO.

**Existing customer data modified:** NO. Only local source, the dedicated local test site, and synthetic fixtures were used. No external services, real customer accounts, production records, historical repair, or Business reassignment was performed. Test reparenting probes affect synthetic children and are rolled back. No historical customer relationship scan was run; no claim about historical data cleanliness is made.

**Regression risk:** MEDIUM. This guard covers shared child persistence and introduces ownership queries/locks. Existing same-tenant workflows pass; the documented unrelated baseline remains red.

## Is FIX-001 fully closed?

**YES**, subject to the final full-suite comparison below: the original Business takeover remains blocked, foreign child modification/deletion/reparenting and forged metadata are denied, supported same-tenant workflows pass, and no equivalent unresolved child-parent takeover remains known in the reviewed Kivo child tables and supported server interfaces. This is closure of FIX-001, not a declaration that the remaining remediation plan is complete. FIX-002 was not started.
