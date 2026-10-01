# Kivo Pilot Integrity release gate

Date: 2026-10-01. Decision: **ACCEPTED FOR NEXT DEVELOPMENT BATCH**. This validates a development foundation, not production certification. No push or deployment was authorized or performed.

Starting HEAD: `34c0ac96aa2041d748aa11a352744914634a4b51`.
Final commit: see the commit containing this report; recording its own SHA here would change that SHA.

## Diff and preservation

Reviewed the full tracked diff, empty staged diff, all new integrity implementation/patch/test files, schemas, prior evidence, documentation and untracked-file inventory. Application changes cover KIVO-PILOT-001–004. No unrelated refactoring, runtime tenant identifiers, embedded credentials, secrets or temporary application debugging were found. Environment paths in diagnostic evidence describe the validation environment; application implementation has no local-path dependency.

One release-gate correction was reported before editing: the pre-existing `saas-access.js` used website-only `frappe.ready` in Desk, causing an uncaught exception and preventing subscription banner/read-only UI initialization. Its initializer now uses `$(document).ready`. A real Chromium check verified banner insertion and the existing read-only route handler using a synthetic boot policy; persisted billing/subscription transitions were not tested. No product feature was added.

Five pre-existing user files remain outside the integrity commit, untouched:

```text
 M docs/DEPLOYMENT.md
 M docs/PRODUCTION.md
?? deploy/backup.sh
?? deploy/compose.operations.yaml
?? docs/RELEASE_OPERATIONS.md
```

Historical invoice identifiers and populated Product identifiers are preserved. Empty optional identifier fields normalize to NULL for unique-index semantics. Financial amounts, timestamps, posting state, balances and links are not rewritten by these patches. Payment migration changes recognized aliases/case/whitespace only. Unknown historical payment values remain unchanged.

Duplicate preflight precedes DDL; scoped indexes are installed before old global indexes disappear during model sync. Invoice counters never rewind. MariaDB DDL commits mean maintenance/no-writer migration remains necessary. Tenant permission hooks remain intact. Customer context authorizes the stored Customer before the server ledger aggregate; the browser no longer sums a truncated transaction list. Existing server fiscal calculation remains authoritative.

## Fresh installation: PASS

Used a newly created disposable MariaDB database/site, `kivo-gate-fresh.localhost`, with repository procedure from `docs/ONBOARDING.md`. Secrets were generated/read outside Git and passed privately to subprocesses; the following credential placeholders denote the actual protected arguments, not literal passwords:

```sh
bench new-site kivo-gate-fresh.localhost --db-host mariadb \
  --db-root-password "$LOCAL_DB_ADMIN_PASSWORD" --admin-password "$GATE_ADMIN_PASSWORD"
bench --site kivo-gate-fresh.localhost install-app creditflow
bench --site kivo-gate-fresh.localhost migrate
bench --site kivo-gate-fresh.localhost clear-cache
env/bin/python /tmp/kivo-release-gate/setup_fixtures.py kivo-gate-fresh.localhost
bench --site kivo-gate-fresh.localhost serve --port 8005 --noreload
LD_LIBRARY_PATH=/tmp/kivo-release-gate/libs/usr/lib/x86_64-linux-gnu \
  node /tmp/kivo-release-gate/setup_browser.cjs
```

All installation/migration commands exited 0. All 33 app DocTypes exist. Install hooks and registered pilot patches succeed without pre-existing business rows. Product has `(business, reference)` and `(business, legacy_product_id)` unique indexes; Sale has `(business, invoice_number)` uniqueness, with old global uniqueness absent.

Initial Business creation succeeded. Both Businesses accepted the same reference. Both independently issued `FAC-2026-00001`, `00002`, `00003`. Customer context returned debt `14.000` and available credit `86.000` against limit `100.000`. Canonical metadata was verified for Sale, Payment, Purchase and Supplier Payment. The real browser setup wizard completed successfully.

Fixtures were created using real document controllers/submission on this disposable site. An additional pair of synthetic Businesses supported a complete browser rerun after the one-line Desk correction. No production or staging migration was performed.

## Upgrade from starting HEAD: PASS

Installed the actual starting-HEAD application from a temporary archive, without changing the shared app checkout:

```sh
mkdir -p /tmp/kivo-release-gate/baseline
git -C apps/creditflow archive 34c0ac96aa2041d748aa11a352744914634a4b51 \
  | tar -x -C /tmp/kivo-release-gate/baseline
PYTHONPATH=/tmp/kivo-release-gate/baseline bench new-site kivo-gate-upgrade.localhost \
  --db-host mariadb --db-root-password "$LOCAL_DB_ADMIN_PASSWORD" --admin-password "$GATE_ADMIN_PASSWORD"
PYTHONPATH=/tmp/kivo-release-gate/baseline \
  bench --site kivo-gate-upgrade.localhost install-app creditflow
PYTHONPATH=/tmp/kivo-release-gate/baseline env/bin/python \
  /tmp/kivo-release-gate/setup_fixtures.py kivo-gate-upgrade.localhost
env/bin/python /tmp/kivo-release-gate/upgrade_snapshot.py
bench --site kivo-gate-upgrade.localhost migrate
# Remove only the deliberately injected synthetic same-Business conflict.
bench --site kivo-gate-upgrade.localhost migrate
bench --site kivo-gate-upgrade.localhost migrate
env/bin/python /tmp/kivo-release-gate/check_upgrade.py
```

Baseline install used the archived Python package, verified by its imported source path. Fixtures included two Businesses, Products, four Customers, two Suppliers, six posted historical Sales, two customer Payments, two partial Purchases and generated Supplier Payments, plus stock/customer/supplier ledger postings.

The intact starting schema globally forbids duplicate references across Businesses. To exercise preservation of an imported legacy catalogue, the fixture deliberately removed that old reference index before assigning `ABC123` to both Businesses. It then injected one same-Business duplicate. The first actual `bench migrate` exited 1 with `Cannot scope Product.reference: duplicate identifiers within a Business`; no new scoped indexes or successful patch log were written. Removing only the injected conflict allowed the same migration to exit 0. Full replay exited 0; patch entry points also replayed harmlessly.

Exact before/after comparison covered **40 historical rows across 11 DocTypes**. Only **eight payment-method aliases** changed to `BANK_TRANSFER`; all other fields and links matched exactly. Historical Business A invoices stayed `00001/00002/00003`; Business B stayed `00004/00005/00006`. Their next invoices were respectively `00004` and `00007`. Cross-Business duplicate references survived, with scoped database uniqueness enforced afterward.

The fixture/check harness initially needed corrections for working directory, Purchase `paid_amount`, Business snapshot filtering, and Patch Log's `patch` field. Those were local validation-script corrections. Final snapshot comparison, actual migration failure/retry/replay, schema and patch-log checks passed; no application migration defect was found. Raw before/after snapshots and commands remain outside Git under `/tmp/kivo-release-gate/`.

## Real browser acceptance: PASS

Chromium 153 via Playwright, real Desk forms/events and HTTP save/submit endpoints, against the explicitly bound disposable-site server. Browser/bootstrap dependencies were installed/extracted locally; no browser behavior was mocked.

Commands (protected credential file read by the harness):

```sh
LD_LIBRARY_PATH=/tmp/kivo-release-gate/libs/usr/lib/x86_64-linux-gnu \
  node /tmp/kivo-release-gate/browser_final.cjs
LD_LIBRARY_PATH=/tmp/kivo-release-gate/libs/usr/lib/x86_64-linux-gnu \
  node /tmp/kivo-release-gate/browser_startup_auth.cjs
```

| Check | Observed result |
|---|---|
| Product A/B `ABC123` | Both real forms save successfully |
| Duplicate `ABC123` in A | HTTP 417; visible same-Business error; no duplicate saved |
| CASH/paid Sale | Total 10.000, paid 10.000, outstanding 0.000; submitted |
| CREDIT Sale | Total/outstanding 10.000; submitted; payment fields correctly cleared |
| MIXED Sale | Total 10.000, paid 4.000, outstanding 6.000; submitted for CASH, BANK_TRANSFER, CHEQUE, CARD, OTHER |
| Visual invoice numbers | A and B both displayed `FAC-2026-00004`; subsequent A numbers advanced independently |
| Customer debt/credit | Form context debt 14.000, available 86.000; visible warning projected 104.000 against limit 100.000 |
| Change customer | Context refreshed to debt 0.000, available 200.000 |
| OWNER browser authorization | Own customer succeeded; foreign customer and forged Business combinations returned 403 |
| Canonical payment options | Coherent across Sale, Payment, Purchase, Supplier Payment; no BANK TRANSFER alias |
| Desk correction | Banner and read-only route callback initialized; zero uncaught page errors on the final startup/auth check |

Available credit is validated in the actual form's server-returned context; the existing form does not add a separate available-credit display. Submitted outstanding values and warning debt agree with server results. No UI feature was added.

The complete post-correction form/retry run passed its transaction assertions, then its tenant harness queried before `frappe.call` loaded. The separate final startup/auth run waited for Desk readiness and passed both manipulated requests and own balance checks. Earlier harness corrections concerned Frappe's default blank grid row, error-save promise semantics, and CREDIT's cleared payment method. These were not product fixes or omitted acceptance cases.

Console/server logs were inspected. Expected negative-test statuses: 417 duplicate, 403 denied tenant access, 500 wrapped MariaDB 1020 during the conflict. The standalone `bench serve` has no Socket.IO proxy/service, so polling produced 404/connection-error noise; realtime transport is not certified by this gate. The migration scheduler warning about missing upstream `duckdb_sync.cleanup_old_syncs` remains. No unexplained transaction/server error remains.

Screenshots and raw logs are retained outside Git under `/tmp/kivo-release-gate/`: `customer-credit.png`, `invoice-b.png`, `retry-message.png`, `readonly-startup.png`, `browser-final.log`, `browser-startup-auth.log`, `serve.log`.

## Retry/failure behavior: PASS

Real browser submission overlapped an independent MariaDB winner transaction with snapshot isolation enabled. The contender received HTTP 500 / `QueryDeadlockError` wrapping MariaDB 1020 in `tabSeries`. The visible dialog instructed: “Please try again.”

Before retry, database checks confirmed contender draft status 0, invoice NULL, zero stock effects and zero credit effects. Reloading the normal form and submitting again succeeded with `FAC-2026-00012`. Exactly one intended contender Sale, one stock OUT posting and one credit posting exist; its invoice is unique within the Business. No Payment was generated by this credit flow. The winner had committed `00011`; the aborted allocation did not consume an invoice. No generalized retry infrastructure was added.

MariaDB integration tests separately cover default snapshot retry, overlapping success with snapshot isolation disabled, distinct Business counters, and Product reference/legacy-ID collision arbitration.

## Final regression

After browser validation and the startup correction, rerun each command below on `kivo-fix001-tests.localhost`:

```sh
bench --site kivo-fix001-tests.localhost run-tests --module <module>
node --test apps/creditflow/creditflow/tests/test_sale_form.cjs
node --check apps/creditflow/creditflow/public/js/saas-access.js
git -C apps/creditflow diff --check
```

| Module | Tests |
|---|---:|
| creditflow.tests.test_pilot_integrity | 9 |
| creditflow.tests.test_pilot_integrity_concurrency | 4 |
| creditflow.tests.test_pilot_integrity_migration | 2 |
| creditflow.tests.test_sale_fiscal_invoice | 13 |
| creditflow.tests.test_customer_payment_workflow | 11 |
| creditflow.tests.test_purchase_payment_modes | 15 |
| creditflow.tests.test_onboarding_imports | 9 |
| creditflow.creditflow.doctype.product.test_product | 7 |
| creditflow.creditflow.doctype.sale.test_sale | 35 |
| creditflow.creditflow.doctype.payment.test_payment | 6 |

**111 integration tests passed, zero skips; 11 Sale-form tests passed.** JavaScript syntax and `git diff --check` passed. Final outcomes are recorded in `evidence/KIVO_PILOT_RELEASE_GATE_TESTS.txt`. Staged review also found and removed trailing whitespace in four diagnostic-log lines; both staged and unstaged checks then passed.

## Limits and residual risks

Full app/Frappe suites, production-volume migration timing, full immutable Docker/image fresh-server installation, realtime Socket.IO transport, push, deployment and production-data migration: **NOT RUN**. This gate used the repository-supported fresh Bench procedure and isolated synthetic sites. Broader suites and deployment are beyond this requested gate; database migration sizing and full infrastructure validation remain future release work.

MariaDB snapshot isolation can require manual whole-request retry; this is safe and actionable in the observed form. DDL recovery requires writes stopped and forward completion; restoring global uniqueness after cross-Business duplicates exist is not a safe code-only downgrade. Existing unknown payment values still require target-specific operational review. Fresh and upgrade disposable sites are retained as test environments; no customer database was restored or modified.
