# PILOT P1 TRIAGE

### Status

PARTIAL

### Finding

P0: **0** per LAST_P0_RESULT.md. All 14 P1s reviewed against directly relevant current code.

Planning basis: invite-only **unpaid** pilot with real trading data. Deferring billing requires server-enforced checkout denial, verified under B-14; it is not currently proven. Paid checkout promotes B-10/11/12 to A. No assumed waiver for stock, debt or refund integrity.

Paths below are relative to `apps/creditflow/creditflow/`; `D/` means `creditflow/doctype/`. Sizes are engineering estimates including focused tests: S ≤1 day, M 1–2 days, L >2 days; operational work depends on access.

### Root cause

Financial checks remain incomplete at lifecycle/concurrency boundaries; UI totals diverge from server fiscal totals. Deployment and recovery acceptance remains undocumented. P0 authorization fixes do not repair these P1 behaviors.

### Validation

Read-only source recheck; no new tests, database queries, runtime deployment checks or code changes. Prior FIX evidence: **414 total / 404 passed / 5 failures / 5 errors**, all ten documented baseline issues; not fresh pilot certification. FIX-001 and LAST_P0 reports retain P0 closure.

### Finding classification

| ID | Class | Current evidence / pilot judgment |
|---|---|---|
| B-02 | A | `public/js/business_defaults.js::recalculate_sale_total/sync_sale_payment_fields` still uses quantity × price and sets CASH paid amount from HT total; taxed checkout/reload remains wrong. |
| B-03 | C | `D/product/product.json` globally uniques reference/legacy_product_id; `onboarding.py::_validate_row` rejects global SKU duplicates. Accept only with assisted, collision-checked, tenant-prefixed pilot references and documented source-ID mapping; no historical renaming. If original SKU preservation is required, promote to A. |
| B-04 | A | `D/product/product.py::validate_primary_uom` checks existence/active only; changing base unit can reinterpret posted inventory. Shared catalogue fix does not close this. |
| B-05 | A | `D/stock_movement/stock_movement.py::validate_source_integrity` returns for manual movements without checking availability. Ordinary OUT can create negative stock. |
| B-06 | A | **Assurance gate, race not reproduced.** `D/sale/sale.py::get_available_stock/validate_credit_limit`, `D/payment/payment.py::get_outstanding_debt`: aggregate reads remain nonlocking despite resource locks elsewhere. Need concurrent committed-invariant proof. |
| B-07 | A | `D/sale_reversal/sale_reversal.py::before_submit` posts credit/stock; `dashboard.py` deducts reversed sale.amount_paid without confirmed refund. Valid customer credit is not itself corruption; cash/obligation semantics remain unsafe. |
| B-08 | A | `D/sale_return/sale_return.py::validate_refund_update` is validate-only; Frappe update-after-submit skips validate. Refund fields allow_on_submit; `hooks.py` adds child authorization, not refund/subscription validation. |
| B-09 | A | Same controller `calculate_from_original` rounds each return independently; `validate_cumulative_quantities` caps quantity, not money. Prior 3.036 versus 3.035 probe remains applicable; not rerun. |
| B-10 | B | `billing/api.py::konnect_webhook` accepts GET; `billing/service.py` sets no commit flag; `apps/frappe/frappe/app.py::sync_database` rolls back GET. Fix before accepting subscription payments. |
| B-11 | B | `billing/service.py::create_checkout` calls provider before local commit and invites retry after ambiguous failure. Fix before enabling paid checkout. |
| B-12 | B | **Race unproven.** `billing/service.py::_lock_payment/_activate` locks payment identity, then calculates subscription term from loaded state. Timestamp protection does not establish eventual application of both paid terms. |
| B-13 | A | Local common config still developer_mode=1; development site allow_tests=true. Compose/runbooks exist, but target production deployment is not verified. Local settings do not prove a separate remote site is unsafe. |
| B-14 | A | `subscription.py::require_feature/get_entitlement`, `onboarding.py::commit_csv` and `billing/service.py::_amount/create_checkout` require an explicit pilot access/payment policy. Current live plan/entitlement/provider values not queried; historical zero/empty values are not asserted current. |
| B-15 | A | `docs/PRODUCTION.md`, `docs/PRODUCTION_READINESS.md` and `docs/DEPLOYMENT.md` prescribe recovery and operations; no completed restore/mail/restart acceptance artifact supplied. Documentation is not execution evidence. |

### Class A acceptance work

| ID | Risk / component | Test needed | Fix size | Dependencies |
|---|---|---|---|---|
| B-02 | Wrong payment/debt; Sale UI | Taxed CASH119/MIXED69, discounts, async edits, submit/reload; match server millimes, no submitted mutation | M | Existing server fiscal calculation |
| B-04 | Historical stock meaning changes; Product | Reject base-unit changes after any history, including zero balance; unused Product allowed | S | None; preserve UOM use |
| B-05 | Negative stock; manual OUT | Stock1/OUT2 rejected without effects; simultaneous OUT1/Sale1 has safe loser | S–M | B-06 common stock boundary |
| B-06 | Oversell/overpayment/limit breach; ledgers | Two connections with forced stale snapshot: last unit, same debt payment, credit limit, return races; cross-tenant control | L if failing | Isolated DB; inspect indexes only if needed for measured fix |
| B-07 | False cash/hidden obligations; reversal/dashboard | Paid reversal cannot imply unrecorded refund; customer credits shown correctly; preserve historical totals | M for bounded restriction | B-08/09 for safe return alternative; B-06 |
| B-08 | Invalid/repeated refund completion; Sale Return | Actual after-submit API: missing method/date, reopen, completed edits, expired/foreign/STAFF rejected; valid completion once, no stock/debt repost | S–M | Existing tenant guard; coordinate B-06 |
| B-09 | Excess fiscal credit/refund; Sale Return | Three split returns, fractional quantities/taxes: cumulative HT/TVA/TTC capped, final residual exact | M | Source serialization; B-06 proof |
| B-13 | Unreliable/unsafe public serving; deployment | Clean pinned deploy with Kivo/assets/P0 metadata; HTTPS, routing, workers, scheduler, private services, dev flags off | M–L ops | Target host/domain/secrets; final enabled fixes |
| B-14 | Broken onboarding/unintended charging; plans | Fresh pilot trial/import, intended entitlements, expiry; server rejects paid checkout without provider call | S–M/config | Pilot policy and target configuration; B-10/11/12 if paid |
| B-15 | Unrecoverable data/service failure; operations | Isolated DB+files+keys restore and ledger reconciliation; restart, worker/scheduler, mail verification and public smoke | M ops | B-13; off-host backup and agreed recovery targets |

### Changes

Only `apps/creditflow/docs/PILOT_P1_TRIAGE.md`. Application code/data unchanged.

### Remaining risk

C requires explicit pilot acceptance and import preflight; B requires tested exclusion from the pilot. Neither is a claim of remediation. No D: none of these 14 is fully stale/fixed; B-07 and provisional findings are narrowed above. Existing fiscal/navigation failures remain disclosed, outside this 14-item triage.

**A/B/C/D counts: 10 / 3 / 1 / 0.** Paid pilot: **13 / 0 / 1 / 0**.

**Ordered pilot blockers:** B-14 (scope/access) → B-04 → B-02 → B-06 → B-05 → B-08 → B-09 → B-07 → B-13 → B-15. Prepare deployment in parallel; final acceptance follows the enabled workflow fixes.

**3-day launch feasibility: RED.** Ten gates include unproven concurrency and operational recovery; source evidence does not support promising closure in three days.
