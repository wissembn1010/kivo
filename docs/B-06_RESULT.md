ID: B-06

Status: PASS — CLOSED for the triage's specified concurrency acceptance.

Root cause: Parent row locks did not refresh nonlocking REPEATABLE READ ledger/existence queries. A contender could retain its pre-commit view after another transaction posted. Credit-limit and return/payment debt decisions also lacked a shared Customer lock. The correction uses current locking reads for stock, customer debt, cumulative returns and return/reversal exclusion, with Customer/source-Sale serialization. Existing fiscal, refund, override, permission and posting rules remain unchanged.

Files changed (relative to apps/creditflow):

- creditflow/creditflow/doctype/sale/sale.py — current stock/debt reads and locked Customer credit-limit read; existing B-02 work preserved.
- creditflow/creditflow/doctype/payment/payment.py — Customer lock before current debt validation.
- creditflow/creditflow/doctype/sale_return/sale_return.py — source lock, current exclusion/quantity/debt-allocation reads and shared Customer lock.
- creditflow/creditflow/doctype/sale_reversal/sale_reversal.py — current, source-serialized return/reversal exclusion.
- creditflow/tests/test_posting_concurrency.py — twelve regressions/controls.
- docs/B-06_RESULT.md — this report.

Tests added: Twelve real-database tests. Two connections overlap transactions: establish contender snapshot, submit/commit winner, then submit and commit/rollback contender; inspect persisted stock/debt/source state. Covers last-unit Sales, same-debt Payments, credit limits, duplicate full-quantity returns, Payment/Return in both orders, Return/full reversal in both orders, duplicate full reversal, valid concurrent Sales, partial-return debt allocation, and tenant-scoped reads/foreign-link rejection. No ledger-query mocks. Initial eight regressions all failed before the application correction, including successful invalid commits and a -100 customer balance; all now pass.

Focused tests: 12/12 passed, 2.090s. Command: bench --site kivo-fix001-tests.localhost run-tests --app creditflow --module creditflow.tests.test_posting_concurrency. Logs: /tmp/kivo_b06_before.log and /tmp/kivo_b06_focus.log. Harness is guarded to this dedicated synthetic site. Its temporary secondary connection disables MariaDB's optional innodb_snapshot_isolation to exercise ordinary InnoDB REPEATABLE READ without unrelated naming-counter aborts; no site/global setting changes. Initial harness connection-proxy and draft-pricing fixture issues were corrected before the clean reproduction. This is deterministic overlapping-transaction coverage, not a load benchmark.

Related tests: 105/107 passed. Sales/customer 10/10; customer Payment 11/11; Sale Return 8/9; Fiscal Invoice 11/12; UOM 11/11; permission hardening 10/10; stored Business authorization 19/19; child authorization 18/18; shared UOM 7/7. The two print failures are documented baseline failures. Log: /tmp/kivo_b06_related.log. B-02 JavaScript regressions separately passed 9/9. FIX-001 and prior blocker protections remain intact. git diff --check passed.

Full suite: bench --site kivo-fix001-tests.localhost run-tests --app creditflow — 437 total / 427 passed / 5 failures / 5 errors; 105.648s, exit 1. Log: /tmp/kivo_b06_full.log. Previous documented B-02 baseline: 425 total / 415 passed / 5 failures / 5 errors, /tmp/kivo_b02_full.log. Twelve additional passes.

New regressions: NONE. Compared failure identities and exception/assertion causes directly with B-02: Sale Return print missing 119.000; Fiscal Invoice print missing 180.000; language None != en; branding root None is not true; branding login /desk/kivo != /app/kivo; five TEJ errors (01/02/03/05/06), valid official Operation Code required. No baseline assertion/configuration changed.

Migration required: NO. No schema/index/global isolation changes.

Data modified: Synthetic dedicated-test-site fixtures only, including deliberate commits and scoped cleanup of test-created Business records/children. NO production/customer data, staging/reference configuration, ledger repair or historical backfill.

Remaining issue: None for the specified B-06 cases. B-05 manual-stock validation, B-07 refund semantics, B-08 after-submit refund validation, B-09 split-return rounding and operational gates remain open; none was started or claimed fixed. No broad audit. kivo-debug/fix/test/security guided the bounded reproduction, correction and tenant/transaction checks.

Remaining Class A count: 6, accounting for B-14, B-04, B-02 and B-06 closure.
