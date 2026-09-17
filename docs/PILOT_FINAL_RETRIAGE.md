# PILOT FINAL RETRIAGE

Status: PARTIAL

Scope: B-07/B-13/B-15 only; invite-only unpaid external pilot. P0=0 and seven closures retained. Reviewed PILOT_P1_TRIAGE.md and B-14/04/02/06/05/08/09 PASS reports. Paths below are relative to apps/creditflow/.

| ID | Current status | Evidence | Class | Reason | Fix size |
|---|---|---|---|---|---|
| B-07 | Open; code defect remains | creditflow/creditflow/doctype/sale_reversal/sale_reversal.py::before_submit permits paid reversals without refund confirmation; creditflow/dashboard.py::cash_collected_today subtracts reversed amount_paid. | A | B-08/09 protect Sale Return only. Cash can imply an unrecorded refund. test_sales_customer_flow_regression.py::test_10_customer_ledger_statement_and_balances_reconcile preserves valid -40 customer credit; that alone is not corruption. | M: bounded restriction/reporting correction and regressions |
| B-13 | Deployment unverified | docs/DEPLOYMENT.md §12 remains an unchecked acceptance checklist. Local common developer_mode=1; development allow_tests=true. B-14 certifies staging configuration only. | A | No reviewed evidence certifies the external target; local flags do not prove a remote deployment unsafe. | M–L operations |
| B-15 | Recovery/operations unverified | docs/PRODUCTION.md §§C–G and docs/PRODUCTION_READINESS.md prescribe drills; B-14 records backup creation and captured email, not restore/live delivery. | A | Real trading data requires demonstrated recovery and service continuity. | M operations |

REMAINING CLASS A: 3

Exact fix order: B-07 → B-13 → B-15.

Required before external pilot:
- Exact release deployment: migrations/assets, HTTPS/routing, private services, production flags, worker/scheduler/realtime health.
- Deployed-browser OWNER/STAFF/foreign-tenant checks; login/navigation, taxed CASH/MIXED submit/reload, returns/refund completion and cash reconciliation; target unpaid checkout denial and trial/import/expiry.
- Live verification-email delivery; restart/reboot checks; off-host DB/files/keys restore on an isolated site, ledger reconciliation and agreed recovery targets.
- Confirm target print/navigation behavior against known baseline failures; no waiver implied.

Validation: source/config/report review only; no tests or runtime checks executed. Prior suite: 455 total / 445 passed / 5 failures / 5 errors, unchanged.

Changes: this report only; no code or business data modified.
