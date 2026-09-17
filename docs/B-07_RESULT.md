ID: B-07

Status: PASS — CLOSED for the bounded reversal/cash correction.

Root cause: Sale Reversal accepted sales with upfront payment while posting only stock/debt corrections, without recording a refund obligation/completion. Dashboard cash_collected_today nevertheless subtracted the original amount_paid as though cash had been refunded. Revenue already reverses original TTC once and needed no calculation change.

Cash/refund semantics: Sale Reversal is a non-cash stock/debt correction. New reversals of any sale with positive persisted upfront amount_paid are rejected and directed to the existing Sale Return workflow, including CASH and MIXED sales. Sale Return records REFUND_DUE; only validated REFUNDED + CASH on refund_date reduces reported cash. Bank refunds do not reduce cash. A credit-sale reversal reverses its original debt and stock but preserves subsequent customer Payments; any negative customer balance is valid customer credit, not an implicit refund. Payment Reversal remains an explicit receipt correction, separately removing that receipt once. For a refund of a paid credit sale, use Sale Return before any full Sale Reversal; existing mutual exclusion prevents combining both workflows. Historical paid reversals are not cash-refund evidence: their stock/debt/fiscal documents are left intact, while dashboard cash no longer invents their cash outflow. The dashboard represents net recorded cash, not bank/cashbox reconciliation. No new refund fields or refund execution system introduced.

Files changed (relative to apps/creditflow, this task):
- creditflow/creditflow/doctype/sale_reversal/sale_reversal.py — three-line persisted upfront-payment restriction/comment in validate_original_sale; existing B-06 locks preserved.
- creditflow/dashboard.py — remove the Sale Reversal amount_paid subtraction from cash_collected_today only.
- creditflow/tests/test_reversal_cash.py — six new regression/control tests.
- creditflow/creditflow/doctype/sale_reversal/test_sale_reversal.py — two paid-reversal expectations updated to rejection with unchanged debt/stock.
- creditflow/tests/test_posting_concurrency.py — three reversal-race fixtures use CREDIT sales so they continue exercising supported reversal posting and duplicate/return exclusion.
- docs/B-07_RESULT.md — this report.
Other pre-existing workspace changes preserved. No revenue, source tax snapshot, customer ledger, stock posting, or Payment Reversal implementation changed.

Tests added: Six covering upfront CASH/MIXED rejection without effects; CREDIT reversal with stock/debt/revenue restoration and unchanged tax snapshots; later partial Payment retained as customer credit until explicit Payment Reversal; pending/completed full Sale Return with exactly-once cash reduction and reversal exclusion before/after refund; bank refund cash behavior; synthetic historical paid-reversal reporting without invented refund. Duplicate Sale/Payment Reversal and identical refund-save controls included. Before fix: 6 tests, 2 failures proving paid reversal acceptance and phantom historical cash deduction (/tmp/kivo_b07_before.log). Five existing tests/fixtures adapted to the deliberately restricted workflow, without changing documented baseline failures.

Focused tests: 6/6 passed, 2.002s, /tmp/kivo_b07_focused.log. git diff --check passed.

Related tests: 91/92 passed: sales/customer 10/10; customer Payment 11/11; Sale Returns 8/9 (documented print failure); refund updates 7/7; return rounding 3/3; workspace dashboard 9/9; posting concurrency 16/16; Sale Reversal 7/7; SME reporting 7/7; operational reports 3/3; permission hardening 10/10. Logs: /tmp/kivo_b07_related.log and /tmp/kivo_b07_reversal_final.log. Initial related run exposed the two old paid-reversal expectations; their corrected module rerun passed 7/7 in 1.535s. Sales Report inspection confirmed paid-immediately snapshots are preserved and net revenue reverses TTC once.

Full suite: bench --site kivo-fix001-tests.localhost run-tests --app creditflow — 461 total / 451 passed / 5 failures / 5 errors, 164.906s, exit 1. Log: /tmp/kivo_b07_full.log. Prior B-09 baseline: 455 total / 445 passed / 5 failures / 5 errors. Six additional passes.

New regressions: NONE. Compared all ten failure identities and causes directly with /tmp/kivo_b09_full.log: Sale Return print missing 119.000; Fiscal Invoice print missing 180.000; language None != en; branding root None is not true; branding login /desk/kivo != /app/kivo; five TEJ errors (01/02/03/05/06), valid official Operation Code required. Baseline assertions and configuration unchanged.

Migration required: NO. No schema/configuration changes or data repair.

Data modified: Dedicated kivo-fix001-tests.localhost synthetic fixtures only, including existing concurrency harness commits/scoped cleanup and a directly seeded historical reversal reporting fixture. No production/customer/staging data modified. No external payment/refund executed.

Remaining issue: No B-07 blocker remains under these semantics. Historical cash payouts lacking supported records are not inferred or backfilled; historical cash dashboard figures may therefore change to remove the unsupported subtraction. Deployment/browser and operations acceptance remain B-13/B-15, neither started.

Remaining Class A count: 2 (B-13, B-15).
