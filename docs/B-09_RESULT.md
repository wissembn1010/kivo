ID: B-09

Status: PASS — CLOSED for cumulative split-return fiscal allocation.

Root cause: Each return independently rounded its proportional HT and TVA; cumulative validation capped only quantity. Three fractional returns could allocate tax 0.486 against original 0.485 (TTC 3.036 against 3.035), or HT 2.550 against 2.549. The correction reads submitted quantity/HT/TVA per original item, computes the cumulative rounded entitlement, then subtracts posted money. Amounts are nonnegative and capped by the remaining source amount; final quantity gets the exact residual. TTC remains HT + TVA. Submission uses the existing source Sale lock and a current locking read scoped by Business, Sale and source item; drafts preview the same allocation and are recalculated on submit. Previously posted rows are not rewritten. Existing historical component over-allocation is rejected rather than silently repaired.

Files changed (relative to apps/creditflow, this task):
- creditflow/creditflow/doctype/sale_return/sale_return.py — bounded calculation change; previous B-06 and B-08 corrections preserved.
- creditflow/tests/test_return_rounding.py — three new fractional split-return regressions.
- creditflow/tests/test_posting_concurrency.py — one new stale-snapshot fiscal allocation regression; existing tests preserved.
- docs/B-09_RESULT.md — this report.
All unrelated pre-existing workspace changes preserved.

Tests added: Four. Three split-return cases cover cash refunds, credit debt reduction with no excess refund, and combined upward/downward HT/TVA rounding at fractional quantity 0.5 out of 1.5. All drafts are prepared before any return posts. Tests assert persisted nonnegative amounts, cumulative HT/TVA/TTC caps after each return, exact final residual, financial effects and final customer balance. All three failed before the correction (/tmp/kivo_b09_before.log), after correcting the synthetic cash fixture to provide its required upfront payment. The fourth test overlaps two real transactions using the existing dedicated-site harness: a stale return posts after a competing committed half-return and must see its fiscal allocation; combined amounts match the source and no excess refund appears. No ledger-query mocks.

Focused tests: 3/3 passed, 1.799s, /tmp/kivo_b09_focused.log. New concurrency case also passed with its related module. git diff --check passed.

Related tests: 90/92 passed: posting concurrency 16/16; Sale Returns 8/9; refund updates 7/7; fiscal invoice 11/12; UOM 11/11; stored Business authorization 19/19; child parent authorization 18/18. Both print failures are documented baseline failures. Log: /tmp/kivo_b09_related.log. Module commands used bench --site kivo-fix001-tests.localhost run-tests --app creditflow --module creditflow.tests.<module>.

Full suite: bench --site kivo-fix001-tests.localhost run-tests --app creditflow — 455 total / 445 passed / 5 failures / 5 errors, 162.999s, exit 1. Log: /tmp/kivo_b09_full.log. B-08 baseline was 451 total / 441 passed / 5 failures / 5 errors. Four additional passes.

New regressions: NONE. Compared all ten failure identities and causes directly with /tmp/kivo_b08_full.log: Sale Return print missing 119.000; Fiscal Invoice print missing 180.000; language None != en; branding root None is not true; branding login /desk/kivo != /app/kivo; five TEJ errors (01/02/03/05/06), valid official Operation Code required. Baseline assertions and configuration unchanged.

Migration required: NO. No schema, configuration or historical repair changes.

Data modified: Dedicated kivo-fix001-tests.localhost synthetic fixtures only, including existing concurrency harness commits/scoped cleanup. No production/customer/staging data modified. No historical returns repaired or external refunds executed.

Remaining issue: None for B-09 acceptance. Historical over-allocation, if present, is not repaired by this change. B-07 reversal refund semantics, B-13 deployment and B-15 operational acceptance remain open; no next blocker started.

Remaining Class A count: 3, after B-14, B-04, B-02, B-06, B-05, B-08 and B-09 closure.
