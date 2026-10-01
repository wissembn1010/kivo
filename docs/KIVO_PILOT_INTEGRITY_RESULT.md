# Kivo Pilot Integrity — current repository result

Date: 2026-09-30. Scope: KIVO-PILOT-001 through 004 only.

The four findings were **CONFIRMED at starting HEAD**. Their implementations and scoped acceptance checks are **VERIFIED DONE in the working tree**. Nothing was committed or deployed. This is not a certification of overall pilot/production readiness.

The earlier blocked run supplied no verification evidence. This run restarted Phase 1. The original task text beyond the four findings and current instructions was unavailable in this session; additional acceptance criteria were requested but were not supplied.

## Phase 1: verified environment and baseline

| Check | Result |
|---|---|
| `pwd` | `/workspace/development/frappe-bench` |
| `git -C apps/creditflow rev-parse --show-toplevel` | `/workspace/development/frappe-bench/apps/creditflow` |
| `git -C apps/creditflow branch --show-current` | `kivo-taj-integration` |
| `git -C apps/creditflow rev-parse HEAD` | `34c0ac96aa2041d748aa11a352744914634a4b51` |
| Installed Frappe import | `16.30.0` |
| CreditFlow package / app title | `creditflow`, version `0.0.1`, title `Kivo` |
| MariaDB | `11.8.8-MariaDB-ubu2404` |
| Frappe upstream working tree | Clean; no upstream edits |

Starting `git -C apps/creditflow status --short`:

```text
 M docs/DEPLOYMENT.md
 M docs/PRODUCTION.md
?? deploy/backup.sh
?? deploy/compose.operations.yaml
?? docs/RELEASE_OPERATIONS.md
```

All five pre-existing changes were preserved. No staging, resetting, committing, or editing of these files was performed.

Both apps, Frappe and CreditFlow, are installed on each local site:

| Site | allow_tests | Starting Products / Sales | Final Products / Sales |
|---|---|---|---|
| `development.localhost` | true | 147 / 17 | 147 / 17 |
| `creditflow-staging.localhost` | unset | 1 / 1 | 1 / 1 |
| `kivo-fix001-tests.localhost` | true | 0 / 0 | 0 / 0 |
| `kivo-recovery-test.localhost` | false | 1 / 0 | 1 / 0 |

All database writes, DDL, migrations, committed concurrency fixtures, and tests used only `kivo-fix001-tests.localhost`. Other sites received read-only installed-app/version/count queries. Count equality is supporting evidence, not a content checksum. Synthetic race and migration Businesses remaining after cleanup: zero.

## Discovery and final architecture

Paths below are relative to `apps/creditflow/`.

| Finding | Verified starting paths/symbols and behavior | Starting classification | Implemented behavior / final status |
|---|---|---|---|
| KIVO-PILOT-001 | `creditflow/creditflow/doctype/product/product.json`: global `unique` on `reference` and `legacy_product_id`. `creditflow/onboarding.py::_validate_row`: global SKU existence check. `_resolve` already includes Business. | CONFIRMED | Composite unique indexes on `(business, reference)` and `(business, legacy_product_id)`; controller/import duplicate checks scoped to Business. Optional empty legacy IDs become NULL. Product document names and populated identifiers remain unchanged. **VERIFIED DONE** |
| KIVO-PILOT-002 | `creditflow/creditflow/doctype/sale/sale.py::assign_invoice_number`: shared `FAC-{year}` series; `sale.json`: globally unique invoice number. | CONFIRMED | Transactional Business/year series, Business row serialization, composite `(business, invoice_number)` uniqueness. Display format stays `FAC-YYYY-NNNNN`; different Businesses can issue the same number. Historical numbers remain unchanged. Draft/submit forgery rejected. **VERIFIED DONE** |
| KIVO-PILOT-003 | `creditflow/public/js/business_defaults.js::load_customer_context`: browser sums a list limited to 1,000 transactions. `customer.py::get_ledger_balance` already aggregates all posted ledger rows. | CONFIRMED | New `creditflow/checkout.py::get_customer_context` checks stored Customer read permission and Business, then returns server-aggregated debt, limit, available credit, and price type. Browser checks request generation, Customer, and Business; stale contexts are cleared. **VERIFIED DONE** |
| KIVO-PILOT-004 | Purchase metadata/controller uses `BANK TRANSFER`; Sale/Payment/Supplier Payment use `BANK_TRANSFER` with different normalization. | CONFIRMED | `creditflow/payment_methods.py` supplies canonical normalization and explicit supported sets. All four controllers accept the recognized legacy alias and store `BANK_TRANSFER`. Purchase Select metadata is canonical. Sale and Payment share the customer vocabulary, including CARD. **VERIFIED DONE** |

Dependency order implemented: Product constraints/imports → invoice counters/migration → checkout retrieval/browser → payment normalization → acceptance and migration/concurrency checks.

Customer vocabulary: CASH, BANK_TRANSFER, CHEQUE, CARD, OTHER. Supplier vocabulary: CASH, BANK_TRANSFER, CHEQUE, TRAITE, OTHER. TRAITE remains supplier-only; CARD is customer-only. Existing CASH-sale restriction, customer cheque reference requirement, and Purchase-generated Supplier Payment flow remain enforced. Refund vocabulary/workflow is unchanged.

## Transaction and tenant boundaries

Product existence checks provide early errors; MariaDB composite unique indexes arbitrate races, including concurrent legacy-ID collisions. Matching identifiers in different Businesses are allowed. All linked records continue to use globally unique internal document names, not reference strings.

Invoice series keys include a SHA-256 digest of the exact Business identity and the invoice year. The allocator locks the Business row before calling installed Frappe `getseries`, which locks/increments `tabSeries` within the caller's transaction. There are no allocator commits. Failed submissions roll back the counter and posting effects. The same Business lock serializes different invoice years as well; this is a deliberate simple locking boundary.

Checkout is a read-only preview, not a credit reservation. It authorizes the stored Customer before the aggregate query; a supplied Business cannot grant access. OWNER/STAFF own-tenant reads succeed; foreign customers, mismatched Business, and Guest fail. Only submitted ledger rows contribute, with DEBIT positive and CREDIT negative. Three-decimal strings preserve money precision. A limit of zero means unlimited, matching `Sale.validate_credit_limit`, so `available_credit` is NULL. A negative available credit indicates an existing excess. Sale submission retains its existing locked credit checks.

Payment normalization runs during ordinary validation before Frappe Select validation. Migration changes only recognized representations, not amounts, posting status, timestamps, balances, or links. Unknown historical values remain intact and require separate review if present on a future target.

## Migration and recovery implications

- Pre-model-sync patch: `creditflow.patches.v16_0.scope_pilot_identifiers` calls `pilot_integrity.ensure_scoped_identifiers`. All three duplicate checks run before the first DDL; duplicates within a Business cause a clear failure.
- Scoped indexes are installed before model sync removes the old single-column constraints. Empty optional product legacy IDs and empty invoice fields become NULL. Product names, populated identifiers, posted invoice numbers, and fiscal/ledger amounts are preserved.
- Existing `FAC-year-sequence` invoices seed each Business/year counter to at least that Business's historical maximum. Seeding never rewinds an existing counter. Case/trailing-space variants matching MariaDB uniqueness are included without rewriting their stored number. Non-FAC historical identifiers are retained.
- Post-model-sync patch: `creditflow.patches.v16_0.normalize_payment_methods` normalizes recognized aliases, case, and whitespace across Sale, Payment, Purchase, and Supplier Payment.
- `creditflow.install.after_install` also installs scoped constraints. Its actual hook was exercised twice on the isolated existing site. A complete fresh-site installation was **NOT RUN**.
- DDL implicitly commits in MariaDB. Any eventual target migration needs application writes/workers stopped, a suitable backup, and normal Frappe migration ordering. No such target operation was performed here.
- Patches are replayable. A partial DDL failure needs forward completion while writes remain stopped. A code-only rollback is unsafe after cross-Business duplicate identifiers/invoices have been accepted: old global constraints cannot simply be restored. Do not rename historical documents to accommodate rollback; use a compatible forward repair or a separately approved restore.

## Executed acceptance checks

Final successful logs from this implementation run are stored in [evidence/KIVO_PILOT_INTEGRITY_TESTS.txt](evidence/KIVO_PILOT_INTEGRITY_TESTS.txt). Final schema, patch log, cleanup and site counts are in [evidence/KIVO_PILOT_INTEGRITY_STATE.json](evidence/KIVO_PILOT_INTEGRITY_STATE.json).

Common integration command:

```bash
bench --site kivo-fix001-tests.localhost run-tests --module <module>
```

| Module | Tests | Result |
|---|---:|---|
| `creditflow.tests.test_pilot_integrity` | 9 | PASS |
| `creditflow.tests.test_pilot_integrity_concurrency` | 4 | PASS |
| `creditflow.tests.test_pilot_integrity_migration` | 2 | PASS |
| `creditflow.tests.test_sale_fiscal_invoice` | 13 | PASS |
| `creditflow.tests.test_customer_payment_workflow` | 11 | PASS |
| `creditflow.tests.test_purchase_payment_modes` | 15 | PASS |
| `creditflow.tests.test_onboarding_imports` | 9 | PASS |
| `creditflow.creditflow.doctype.product.test_product` | 7 | PASS |
| `creditflow.creditflow.doctype.sale.test_sale` | 35 | PASS |
| `creditflow.creditflow.doctype.payment.test_payment` | 6 | PASS |

Total: **111 integration tests passed**, no skips in these runs.

```bash
node --test apps/creditflow/creditflow/tests/test_sale_form.cjs
```

**11 passed**, including out-of-order customer replies, repeated same-customer refreshes, Business change, customer clearing, and existing taxed-total/save sequencing tests. This is a Node form harness, not live browser evidence.

Focused Python compilation: PASS. `git -C apps/creditflow diff --check`: PASS, exit 0, no output.

### Real MariaDB concurrency evidence

The tests use independent database connections and synchronized overlapping transactions, with a contender snapshot established before the winner writes. No process-local lock or mocked database decides the result.

| Scenario | Connections | Isolation | Observed committed outcome |
|---|---|---|---|
| Same-Business invoice submissions | 45 / 48 | REPEATABLE READ, snapshot isolation OFF on contender | Contender waits; both post; numbers 00001 and 00002; exactly two stock OUT postings |
| Default-settings invoice submissions | 45 / 46 | REPEATABLE READ, snapshot isolation ON | Contender waits then receives `QueryDeadlockError` wrapping MariaDB 1020; rollback leaves its draft and zero posting effects; fresh transaction retry posts 00002 |
| Different-Business invoice allocation, existing counter keys | 45 / 47 | REPEATABLE READ, snapshot isolation ON | Allocator completes before winner commit; both Businesses allocate 00001 from separate counters |
| Same-Business product reference collision | 45 / 49 | REPEATABLE READ, snapshot isolation ON | Contender waits on uniqueness; one committed Product, one `UniqueValidationError`; other Business accepts the same reference |
| Same-Business legacy product ID collision | 45 / 50 | REPEATABLE READ, snapshot isolation ON | Same one-winner outcome; other Business accepts the same legacy ID |

Different-Business allocator testing isolates fiscal numbering from upstream global Stock Movement naming. Initial missing-series gap locks can still make unrelated first allocations wait; a preliminary full-posting experiment also waited on unrelated shared naming. Neither is evidence of a shared fiscal counter. No claim of wait-free end-to-end posting is made.

### Migration evidence

`bench --site kivo-fix001-tests.localhost migrate`: PASS on initial patch application and full migration replay, exit 0. Both new patches appear in Patch Log. Final indexes contain exactly the scoped columns and no old single-column product/invoice unique indexes.

The two DDL integration tests reconstruct the old global constraints, use actual posted CASH/CREDIT Sales, Customer debt and Stock Movement rows, run the registered patch entry points plus real model sync, and compare historical rows. Only the intended empty-ID/alias normalization differs. Historical invoice 00073 stays intact; next invoices are 00074 and 00075 after migration/replay/install-hook invocation. Duplicate preflight rejection leaves schema/data unchanged until the deliberately injected duplicate is removed by test cleanup.

Test development had harness/fixture failures: a database LocalProxy cleanup error was corrected; reloaded race Sales were changed to supply explicit base_price; exception assertions were corrected for Frappe's wrapped MariaDB error; imported test-class discovery was removed. Final tests are green. Only explicitly identified synthetic fixtures from interrupted harness runs were cleaned.

## Residual risks and checks not run

1. MariaDB snapshot isolation ON can require a **fresh whole-transaction retry** after error 1020. Rollback/retry integrity is demonstrated; automatic request retry was not added. Standard InnoDB locking success is demonstrated separately.
2. First-time missing-series gap locks and upstream document naming can cause incidental cross-Business waits. Counter values and uniqueness remain Business-scoped.
3. Live Desk/browser and HTTP request smoke tests, full fresh-site installation, production-sized migration timing, broader app/Frappe suites, deployment, and production migration: **NOT RUN**. None is implied by the passing scoped tests.
4. Migration replay emits an upstream scheduler warning about missing `frappe.core.doctype.duckdb_sync.duckdb_sync.cleanup_old_syncs`. Migration exits successfully; no scheduler changes were made.
5. Original task-specific acceptance criteria beyond the resumed instructions were unavailable. The statuses below apply to the four supplied findings and the executed evidence, not undisclosed criteria.

| Item | Final status |
|---|---|
| KIVO-PILOT-001 | VERIFIED DONE — working tree, isolated schema and real duplicate races |
| KIVO-PILOT-002 | VERIFIED DONE — scoped allocation, preservation, rollback and fresh-transaction retry evidence |
| KIVO-PILOT-003 | VERIFIED DONE — authoritative aggregate, role/tenant checks and browser-response sequencing |
| KIVO-PILOT-004 | VERIFIED DONE — canonical vocabulary, valid posted customer/purchase flows and migration replay |

Final HEAD: **`34c0ac96aa2041d748aa11a352744914634a4b51`**. Changes remain uncommitted. The checked-in HEAD itself still has the confirmed findings until these working-tree changes are committed. No production data was modified and no deployment was performed.
