# Bank transfer production readiness — 2026-10-08

**Deployment is conditional on the operational checks below.** This review changed application code and dedicated test-site fixtures only. No deployment, production migration, production payment changes or live bank connection was performed.

## Bugs corrected during this review

- Operational receipts stored `CHEQUE` correctly but rendered the storage code instead of the translated label. The receipt now uses an explicit label mapping (`Cheque`, French `Chèque`); canonical storage and the original meaningful assertion are preserved. Other methods and historical unknown labels are covered.
- Browser checkout sends Frappe's `cmd` routing field. The API now accepts only its own exact routing command while still rejecting price overrides and other unsupported arguments.
- The payment-return Python controller had a hyphenated filename, so Frappe did not load its context or tenant authorization. It is now `www/creditflow_payment_return.py`; the public URL is unchanged.
- Native private File downloads allowed the original uploader after reassignment to another Business. A receipt-specific File mixin checks current Business membership, owner role and private storage before allowing downloads; independent administrators retain access.
- Retired Konnect methods could still make requests if called directly. Creation, polling and the underlying request method now fail before network activity. Diagnostics report retired/not ready and expose no active webhook URL. Historical documents and compatibility symbols remain.
- Maintenance now isolates ordinary per-record failures and continues later records. Database deadlocks propagate for whole-job retry rather than continuing after a database-wide rollback. Reconciliation follows the same rule. Resubmitting proof preserves an existing review state and notes.

## Verified application behavior

Review changes: `billing/{api,bank,readiness}.py`, `billing/providers/konnect.py`,
`hooks.py`, `payment_methods.py`, operational Payment controller/receipt format,
the renamed payment-return controller, billing/receipt/provider/readiness test
modules, new `test_bank_jobs.py`, and reusable `bank_browser_support.py` /
`browser_bank_transfer.cjs`. The original implementation's schema and flow
diagram are listed in [the billing architecture report](BANK_TRANSFER_BILLING.md).
Pre-existing deployment/operations edits were preserved.

The final full-app regression result and browser evidence are recorded below. Tests use `kivo-fix001-tests.localhost`, never production. Browser fixtures use synthetic transfers; verification demonstrates application behavior, not receipt of actual funds.

Final command: `bench --site kivo-fix001-tests.localhost run-tests --app creditflow --lightmode` — **559 tests passed in 160.351 seconds**. Log: `/tmp/kivo-bank-production-review-tests-final.log`. This includes all billing, paywall, subscription, operational receipt and job retry regressions. An earlier run hit the framework user-creation throttle during synthetic fixture setup; fixture creation is now isolated from that unrelated quota without changing production signup limits. `git diff --check` and browser script `node --check` also pass.

Final real Chromium rerun: **15 checks passed** against the final application code. Both registered billing jobs completed through native dispatch (`Start`, `Complete`), with reconciliation source unset and payment/subscription queries restricted to the synthetic Business. This proves job execution in isolation, not unattended production scheduling.

| Area | Evidence and scope |
|---|---|
| Checkout and receipts | Real Chromium customer UI: server-priced monthly checkout, exact configured RIB, unique reference, clipboard and private image upload. HTTP tests cover forgery, tenant boundaries, guest access and invalid uploads. |
| Approval and entitlements | Real native Desk review and independent System Manager approval activate the subscription; a protected Customer write succeeds afterward. Backend tests cover duplicate/concurrent approval, rollback and denied self-approval. |
| Unverified requests | New unpaid/expired-trial users stay blocked with pending, proof-submitted, under-review, rejected and expired requests. Browser/local-storage flags cannot authorize backend writes. A still-valid paid period or free trial can legitimately retain access while a renewal awaits verification. |
| Renewal and expiry | Browser annual renewal extends from the existing paid end, preserves access while pending, and rejection does not extend it. Expiry is triggered on the synthetic subscription through real maintenance, then the browser and backend deny protected writes. Cancellation/period behavior is covered by backend tests. |
| Konnect retirement | Direct provider creation/poll/request calls fail before network use; legacy service refresh is inert; callbacks cannot credit a subscription. Existing historical records are retained. |
| Reconciliation | Tests exercise normalized authorized-source records, wrong amounts/missing references, duplicates, ambiguity, source failure and retries. **No real bank source was supplied or tested.** |
| Jobs | Hourly reconciliation and daily maintenance are registered and individually enabled on the test site. Native dispatch is exercised with queries restricted to the synthetic Business. The global test scheduler remains paused. |
| Audit/roles | Customer/admin authorization, immutable billing/transaction/audit writes, sanitized failure logs and successful transition history are tested. System Manager is a trusted administrative boundary; receipts never approve a payment. |

Browser artifacts: `/tmp/kivo-bank-production-review/browser-results.json`, `billing-evidence.json`, `job-evidence.json`, and screenshots `customer-proof-submitted.png`, `admin-payment-verified.png`, `customer-subscription-expired.png`. Temporary credentials are excluded; synthetic records and checkout settings are cleaned/restored after the run.

## Database and rollback review

The bank rollout uses additive Frappe DocType schema synchronization: new Settings, bank transaction and audit records; nullable receipt/review/request-expiry and unique transaction-link fields; subscription reminder marker. No new destructive data patch or status/price rewrite is introduced. Historical provider fields/statuses stay available. Existing plan seed logic inserts missing codes and preserves existing prices.

Existing `patches.txt` entries still execute according to each site's Patch Log. **Production patch backlog and data were not inspected**, so production migration safety requires rehearsal against a sanitized recent clone, including unique indexes, historical payment counts/statuses, plans/prices and backup restoration. The dedicated test-site migration is evidence of framework compatibility, not a production-volume migration or rollback rehearsal.

There is no automatic down migration; MariaDB DDL is not transactionally reversible. Operational rollback should disable new checkout, retain all bank/audit/history tables and use a reviewed forward fix. Do not drop the ledgers or restore gateway code that can activate old callbacks. Any backup restoration must reconcile transfers received since the backup. Production migration and restoration require separate explicit authorization.

## Configuration and operator instructions

- Manual verification is the default. `creditflow_paid_checkout_enabled` must be explicitly enabled to accept new requests. No new banking environment variables are needed for manual mode.
- Configure **Kivo Bank Settings** as System Manager: RIB `2519 0000 0001 5085 8272`; bank and beneficiary names have no invented defaults. Confirm account ownership and transfer instructions with the real banking operator. Policies default to payment expiry 7 days, review reminder 1 day and subscription reminder 3 days; validation accepts 1–365 days. Existing requests preserve their RIB snapshot.
- Leave `creditflow_bank_reconciliation_adapter` unset unless a vetted installed-app adapter consumes an authorized bank API/export. Configuration presence is not connectivity proof. Protect credentials server-side; never use customer proofs as transaction feeds.
- Open **CreditFlow Billing Payment**, filter **Awaiting review**, inspect customer/Business/plan/amount/reference/receipt/history, and check actual booked received funds independently. **Verify received funds** requires the actual bank transaction ID, exact amount/reference, received date, destination where available, confirmation and notes. Requesters and administrators assigned to that Business cannot approve their own requests. Use **Reject** with notes when appropriate. Unmatched bank ledger entries require investigation; late/partial/extra funds do not automatically activate access.
- Confirm real production scheduler, Redis and workers are healthy and the two jobs are registered and not stopped. No production scheduler was enabled in this review. Reconciliation should run hourly; expiration/review reminders daily. In-app Notification Logs are implemented; SMTP/SMS delivery is not demonstrated.

## Remaining release gates and limitations

1. Authorized production migration rehearsal, tested backup restoration, production Patch Log/index/history checks and an agreed retention-preserving rollback plan.
2. Operator confirmation of account ownership, beneficiary/bank details, administrative assignments and the independent funds-check procedure.
3. Production asset build/process reload and scheduler/worker liveness checks after an authorized rollout; shared production recurring execution has not been tested.
4. Live bank reconciliation is unavailable without a legitimate source and a vetted adapter. Manual operation can launch without it; do not advertise automatic bank verification.

Receipt validation is not antivirus scanning. Notifications are native in-app records; guaranteed external delivery is outside the verified scope. Historical LEGACY_ACCESS, valid free trials and administrator-managed perpetual subscriptions remain intentional compatibility policies. Backend entitlement checks were reviewed and covered by the app's tests; this is not a proof that every future/custom endpoint will enforce them. Live bank transfers, production load, cross-bank identifier behavior and production disaster recovery remain untested.
