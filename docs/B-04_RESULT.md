ID: B-04

Status: PASS

Root cause: Product.validate_primary_uom only checked whether the incoming unit existed and was active. Changing primary_unit after stock movements reinterpreted historical base quantities, including when current stock was zero. The correction compares against the uncached persisted Product unit and rejects changes when any Stock Movement exists for that Product. Unchanged units, unused Products and ordinary description/conversion edits remain allowed. No historical records are rewritten.

Files changed:
- creditflow/creditflow/doctype/product/product.py — Product.validate_primary_uom; ten-line history guard.
- creditflow/tests/test_uom_workflows.py — three regressions.
- docs/B-04_RESULT.md — this report.
Paths relative to apps/creditflow. Existing unrelated changes preserved. kivo-fix and kivo-test guided the minimal correction and staged tests. No authentication/tenant rule was changed; no debug skill was needed.

Tests added: Three regressions: direct save rejects a base-unit change with stock history and leaves persisted unit/timestamp/stock unchanged; bulk_update rejects the change after stock reaches zero and preserves sale snapshots; unused Product base-unit changes and ordinary description/conversion edits remain allowed. Before fix: 11 tests, two failures proving successful invalid direct/bulk changes. After fix: both regressions green.

Focused tests: 11/11 passed. Command: bench --site kivo-fix001-tests.localhost run-tests --app creditflow --module creditflow.tests.test_uom_workflows. Logs: /tmp/kivo_b04_before.log, /tmp/kivo_b04_focus.log.

Related tests: 81/81 passed: sales/customer 10, purchase/supplier 15, imports 6, stock movement 6, stored Business authorization 19, child authorization 18, shared UOM 7. Log: /tmp/kivo_b04_related.log. Existing FIX-001 protections remain intact.

Full suite: bench --site kivo-fix001-tests.localhost run-tests --app creditflow: 422 total / 412 passed / 5 failures / 5 errors; 91.069 seconds, exit 1. Log: /tmp/kivo_b04_full.log. Three added passes versus documented 419/409 baseline (/tmp/kivo_b14_accept_full.log).

New regressions: NONE. Same five failure identities/causes: Sale Return print missing 119.000; Fiscal Invoice print missing 180.000; language None != en; branding root None is not true; branding login /desk/kivo != /app/kivo. Same five TEJ tests 01/02/03/05/06 error because a valid official operation code is required. No baseline test expectation or reference configuration changed. git diff --check passed.

Migration required: NO.

Data modified: Synthetic dedicated-test-site fixtures only. NO production/customer data or staging/reference configuration changes. No stock/balance repair or historical UOM backfill.

Remaining issue: None for B-04's existing-history mutation paths. Concurrent first posting versus Product editing remains within the separately tracked B-06 concurrency acceptance; no concurrency closure is claimed. Trusted raw database writes are outside normal Document/API validation. No other blocker started.

Remaining Class A count: 8.
