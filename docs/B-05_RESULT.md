ID: B-05

Status: PASS — CLOSED for manual OUT stock integrity.

Root cause: Manual Stock Movements validated quantity, ownership and source fields but never checked available stock before posting. Manual OUT could overdraw independently or after a competing Sale/manual OUT. A new before_submit guard locks the Product using the Sale lock boundary, then uses a tenant/product-scoped current locking ledger read to reject insufficient stock before persistence. Both MANUAL_ADJUSTMENT and OPENING_STOCK OUT are covered, including direct submitted insertion. Drafts and valid fractional withdrawals remain supported.

Files changed (relative to apps/creditflow, this task):
- creditflow/creditflow/doctype/stock_movement/stock_movement.py — 20-line submission guard.
- creditflow/creditflow/doctype/stock_movement/test_stock_movement.py — one additional draft/replenishment regression; existing uncommitted B-05 tests retained and used.
- creditflow/tests/test_mvp_acceptance.py — assert overdraft rejection, then seed only the synthetic historical ledger status directly to retain negative-stock report coverage.
- docs/B-05_RESULT.md — this report.
Existing creditflow/tests/test_posting_concurrency.py was reused without edits. Other pre-existing workspace changes were preserved.

Tests added: One new draft/replenishment test; one existing MVP test updated. Reused six B-05 tests already present at task start: three stock tests for oversized withdrawals/foreign stock, direct submitted insertion, and fractional stock exhaustion; three overlapping-transaction tests for Sale/manual OUT in both orders and two manual OUTs. The stock test covers both manual reasons. Before the controller fix: 9 stock tests reported 4 assertion failures (including subtests), and 15 concurrency tests reported 2 failures from invalid competing commits. Logs: /tmp/kivo_b05_before.log and /tmp/kivo_b05_race_before.log.

Focused tests: 10/10 Stock Movement tests passed (1.276s, /tmp/kivo_b05_related.log). Includes rejected posting with unchanged persisted draft/ledger count, direct submitted insertion, tenant controls, exact exhaustion, and replenishment before retry. git diff --check passed.

Related tests: 87/87 passed. Concurrency 15/15 (3.986s, /tmp/kivo_b05_concurrency.log); Sales/customer 10/10, Purchase/supplier 15/15, UOM 11/11, onboarding imports 6/6, permission hardening 10/10, stored Business authorization 19/19 (/tmp/kivo_b05_related.log); MVP acceptance 1/1 (3.012s, /tmp/kivo_b05_mvp.log). Concurrency uses the existing two-connection stale-snapshot harness with committed winner and contender commit/rollback on the dedicated synthetic site; it is not a load benchmark.

Full suite: bench --site kivo-fix001-tests.localhost run-tests --app creditflow — 444 total / 434 passed / 5 failures / 5 errors, 171.848s, exit 1. Log: /tmp/kivo_b05_full_final.log. B-06 documented baseline: 437 total / 427 passed / 5 failures / 5 errors. Seven additional passing tests, including six regressions already present in the workspace. Initial full run (/tmp/kivo_b05_full.log) exposed the MVP fixture's reliance on permitting new negative stock; the bounded fixture correction passed focused and full validation.

New regressions: NONE remaining. Compared all ten failure identities and causes directly with /tmp/kivo_b06_full.log: Sale Return print missing 119.000; Fiscal Invoice print missing 180.000; language None != en; branding root None is not true; branding login /desk/kivo != /app/kivo; five TEJ errors (01/02/03/05/06), valid official Operation Code required. Baseline assertions and configuration unchanged.

Migration required: NO. No schema, index, configuration or historical repair changes.

Data modified: Dedicated kivo-fix001-tests.localhost synthetic fixtures only, including existing concurrency harness commits/scoped cleanup and the MVP synthetic historical negative-stock fixture. No production/customer/staging data modified.

Remaining issue: None for B-05 acceptance. Historical negative stock is preserved, not repaired. B-08, B-09, B-07, B-13 and B-15 remain open; no next blocker started.

Remaining Class A count: 5, after B-14, B-04, B-02, B-06 and B-05 closure.
