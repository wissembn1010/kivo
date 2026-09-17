ID: B-08

Status: PASS — CLOSED for submitted Sale Return refund updates.

Root cause: Frappe submitted saves run before_update_after_submit, bypassing validate and the before_validate tenant/subscription gate. Refund fields permit after-submit updates, so required completion details and subscription restrictions were not checked. The existing validator also allowed REFUNDED details to change, and compared child Document identity rather than economic values. The fix invokes the existing tenant/subscription guard and refund validator on the correct lifecycle event, compares normalized persisted field/child values excluding child modification audit timestamps, permits only REFUND_DUE to REFUNDED with positive refund amount and valid method/date, and makes completed refund details immutable. Identical retries are harmless; framework row locking/timestamp checks retain stale-save protection. Financial calculation and posting paths are unchanged.

Files changed (relative to apps/creditflow, this task):
- creditflow/creditflow/doctype/sale_return/sale_return.py — after-submit gate and bounded refund validation correction; existing B-06 edits preserved.
- creditflow/tests/test_refund_updates.py — seven new regression/control tests using existing synthetic return fixtures.
- docs/B-08_RESULT.md — this report.
Other pre-existing workspace changes were preserved.

Tests added: Seven tests exercise actual frappe.client.set_value and frappe.client.save document APIs, without mocking refund validation or authorization: missing method/date rejected through both APIs; OWNER completion with identical retry and no stock/debt repost; completed status/method/date/reference edits rejected; NO_REFUND cannot complete; expired OWNER rejected; STAFF and foreign OWNER rejected; submitted economic/child fields immutable; stale completion payload rejected by framework timestamp protection. These are server API calls, not browser or HTTP transport tests. Initial six tests reproduced 11 failed assertions including subtests before the controller change (/tmp/kivo_b08_before.log). Test-only setup corrections fixed a helper import and prefixed random plan codes to satisfy existing plan validation.

Focused tests: 7/7 passed, 4.149s, /tmp/kivo_b08_related.log. Initial six-test corrected run also passed, /tmp/kivo_b08_focused.log. git diff --check passed.

Related tests: 81/82 passed: Sale Returns 8/9 (documented print baseline); posting concurrency 15/15; subscription foundation 11/11; permission hardening 10/10; stored Business authorization 19/19; child parent authorization 18/18. Log: /tmp/kivo_b08_related.log. Commands used bench --site kivo-fix001-tests.localhost run-tests --app creditflow --module creditflow.tests.<module> for each corresponding module.

Full suite: bench --site kivo-fix001-tests.localhost run-tests --app creditflow — 451 total / 441 passed / 5 failures / 5 errors, 138.325s, exit 1. Log: /tmp/kivo_b08_full.log. B-05 baseline was 444 total / 434 passed / 5 failures / 5 errors. Seven additional passes.

New regressions: NONE. Compared all ten failure identities and causes directly with /tmp/kivo_b05_full_final.log: Sale Return print missing 119.000; Fiscal Invoice print missing 180.000; language None != en; branding root None is not true; branding login /desk/kivo != /app/kivo; five TEJ errors (01/02/03/05/06), valid official Operation Code required. Baseline assertions and configuration unchanged.

Migration required: NO. No schema, configuration, or historical repair changes.

Data modified: Dedicated kivo-fix001-tests.localhost synthetic fixtures only, including existing concurrency harness commits/scoped cleanup. No production/customer/staging data modified. No historical refunds repaired or external refunds executed.

Remaining issue: None for B-08 acceptance. B-09 split-return rounding, B-07 reversal refund semantics, B-13 deployment and B-15 operational acceptance remain open; no next blocker started.

Remaining Class A count: 4, after B-14, B-04, B-02, B-06, B-05 and B-08 closure.
