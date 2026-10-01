# Kivo French Localization Closure Gate

Decision: **FRENCH LOCALIZATION VERIFIED** against the current WSL working tree on 2026-10-01. A focused localization commit is authorized by the closure criteria. No push, deployment, or production-data modification was performed.

## Repository and environment

- Directory: `/home/wissem/projects/frappe_docker/development/frappe-bench/apps/creditflow`
- Branch: `kivo-taj-integration`
- Starting HEAD: `be21c94f86403a6cdc67ae36baa082728d7d0660`.
- All incoming localization changes were uncommitted. Final commit SHA and final status are returned in the accompanying closure response.
- Preserved unrelated files: `docs/DEPLOYMENT.md`, `docs/PRODUCTION.md`, `docs/RELEASE_OPERATIONS.md`, `deploy/backup.sh`, `deploy/compose.operations.yaml`. SHA-256 checks before and after verification confirm all five are byte-for-byte unchanged.
- The host virtualenv points to container-only Python paths. The existing development containers were restarted. `frappe_docker_devcontainer-frappe-1` mounts this exact repository at `/workspace/development/frappe-bench/apps/creditflow`; its Git HEAD and Python import path were verified.
- Testing and browser fixtures used only `kivo-fix001-tests.localhost`, with `allow_tests=True`. Frappe setup flags were completed on this disposable site. Synthetic French customers/products, sales, payments, a return, withholding, and historical negative stock were created there. The negative-stock fixture represents historical report data; it is not a normal stock-posting operation.
- The older acceptance container does not mount this tree and its MariaDB container has a stale bind mount. Its failed test initialization is excluded from successful evidence. The previous fresh-gate site also has database credential errors and was not used for accepted evidence.

## Catalog integrity and repairs

`python3 scripts/validate_translation_catalogs.py` passes:

| Catalog | Unique source/context keys | Deleted baseline keys | Generated Arabic additions | Result |
|---|---:|---:|---:|---|
| fr | 1022 | 0 | N/A | PASS |
| ar | 103 | 0 | 0 | PASS |
| ar-TN | 94 | 0 | 0 | PASS |

Strict UTF-8 CSV parsing validates row lengths, quoting, nonempty values, unique source/context pairs, numbered placeholders, and printf placeholders. The validator compares against the pinned starting HEAD. It never writes catalogs.

The incoming Arabic catalogs contained legacy-generation damage: ar had only 53 rows and ar-TN 90, with deleted Kivo keys and unrelated cached framework additions. Existing translations were recovered from the pinned baseline, retained current values for baseline keys, removed the generated additions, and collapsed the repeated identical email-title key. The repaired Arabic mappings match all baseline mappings exactly. French has 904 added keys and two intentional existing changes: Submit → Valider, Submitted → Validé. No existing French key was deleted.

Additional closure fixes: password accessibility label translated; explicit French entries added for required-field messages, Create/View/Hide, Unit and Legacy; fiscal invoice translates the walk-in customer at display time. A new regression proves that a cash sale created in French stores the source snapshot `Walk-in Customer`, prints `Client de passage` in French, and prints `Walk-in Customer` in English. Actual customer names are preserved.

## Static and internal-value audit

Reviewed custom Python feedback, JS dialogs/actions/form events, DocType labels/descriptions and Select options, report filters/columns/formatters, workspace/sidebar labels, import feedback, signup/login/onboarding, billing/subscription, fiscal/TEJ screens, and the four print templates. Literal `_()`/`__()` references in application Python/JS/templates and DocType labels/descriptions have French catalog coverage. No unwrapped merchant-facing Python `frappe.throw`/`msgprint` literals remain in normal workflows.

| Remaining text/category | Classification | Justification |
|---|---|---|
| SKU, TVA, TND, TEJ, HT, TTC, PM/PP, official TEJ identifiers | INTENTIONAL TERM | Tunisian commercial/fiscal abbreviations or official schema codes |
| Kivo, Konnect; user-entered names, references, notes, plan descriptions | INTENTIONAL TERM / user data | Brand names and entered content are preserved; not automatically rewritten |
| `Begin typing for results.`, result-count announcement, `Last Edited By …`, grid `No.` | UPSTREAM FRAPPE | Framework link widget/sidebar/grid output; `No.` has a framework translation context |
| Raw fieldnames in developer mode; SHA-256/JSON; migration diagnostics | ADMIN/TECHNICAL ONLY | Developer metadata and operational tools, outside normal merchant presentation |
| Webhook throttle message, provider payload text, canonical enum and CSV-schema keys | NOT USER-FACING | Service interfaces and internal schema; not merchant labels |
| Remaining material Kivo English labels in checked normal workflows | KIVO BUG | None after the closure repairs |

Canonical stored payment methods (`CASH`, `BANK_TRANSFER`, `CHEQUE`, `CARD`, `OTHER`), sale modes (`CASH`, `CREDIT`, `MIXED`), stock statuses, transaction types, return/reversal statuses, subscription/access statuses and import types remain English/internal codes. Report formatters translate local display values without mutating row data. Sale Select options retain `value` and translate `label`, with a separate Sale Mode context for CASH. Server checks continue to compare canonical values. No JS/Python comparison against French display labels was found. Stock-report Python and its calculations are unchanged.

## Fresh browser evidence

Real Chromium headless shell driven by Playwright against a running Frappe development server, logged in as the synthetic **OWNER**. Language changed through `creditflow.i18n.set_language`, persisted on that user, then the same user/session replayed in English. Initial browser capture produced 44 screen captures, supplemented by final workflow/print/billing/warning captures after repairs.

Actually inspected in French:

- Dashboard.
- Sale: new, saved draft, submitted CASH/paid, CREDIT, MIXED/partial; payment-method and sale-mode options; credit warning; save, confirmation, submitted-state feedback.
- Customer Payment, Customers, Products, Purchases, Supplier Payment, Suppliers.
- Stock disponible, Stock faible's actual destination (Stock On Hand with show_zero_stock=1), Stock Movement list.
- Customer/Supplier Balances, Customer/Supplier Statement, Product Performance, Sales Report, Purchases Report.
- Import, Business settings, OWNER onboarding, subscription/billing; signup, check-email, invalid verify-signup state.

The initial exploratory Low-Stock capture used a Product-list route; only the supplemental `Low-Stock-final-fr` capture of the actual sidebar destination is accepted as low-stock evidence.

The supplemental browser workflow saves and submits CASH, CREDIT and MIXED sales and a BANK_TRANSFER customer payment through Frappe form actions and French confirmation dialogs. Canonical stored values were asserted. An unsaved sale exceeding the customer's credit limit displays the French warning. It was not submitted.

Stock disponible: Produit, Entreprise, Référence / SKU, Nom du produit, Unité de base, Stock actuel, Prix de vente, Prix minimum, Stock minimum, État du stock, Négatif, Afficher les stocks à zéro, and Afficher uniquement les stocks négatifs were verified. Disabling show_zero_stock removes the zero-stock fixture; negative-only returns nonempty exclusively negative rows. Browser report JSON retains Negative/Out of Stock/In Stock and numeric filter values. Integration tests compare English/French rows and fieldnames unchanged.

Four actual browser print views inspected: **Fiscal Invoice**, **Payment Receipt**, **Sale Return Receipt**, **Withholding Tax Certificate**. French titles, parties, payment terminology, totals and taxes render correctly; HT/TVA/TTC/TND and fiscal codes remain intentional. Final cash-invoice printview rechecked after fixing the English snapshot leak. English invoice replay also passed. No fiscal/accounting computation was modified.

Mobile screenshots actually reviewed at **390 × 844**: Dashboard, Sale, Customer Payment, Stock disponible. Translated controls remain usable; stock and item tables use the framework's horizontal layout. Some narrow grid headers and the new-document title are ellipsized/clipped by existing Frappe column widths. No page-wide overflow or unusable translated form controls observed. No mobile redesign was performed.

English replay: Sale, Customer Payment, Stock disponible, Customer Statement, Supplier Statement, OWNER onboarding; supplemental fiscal invoice and billing. Source English renders again and canonical values remain unchanged.

Runtime evidence: browser boot lang=fr, `__('Current Stock') === 'Stock actuel'`, `__('CASH', null, 'Sale Mode') === 'Au comptant'`; Select option value CASH displays Au comptant for sale mode and Espèces for payment method. English boot and `__('Current Stock') === 'Current Stock'` asserted. Integration runtime translations and context lookup pass.

Frappe's current `translate.py` loads CSV catalogs directly through `get_translations_from_csv` and merges them into cached runtime dictionaries (with optional MO catalogs). No separate CSV translation compilation is required for these changes. Test-site translation caches were cleared safely. **build-message-files was not run; no translation-build success is claimed.**

Browser limitations: report navigation without required customer/supplier filters produces Frappe's `Filter missing` promise error; populated statements render and return data in both languages. The standalone dev HTTP server returns socket.io 404s because a socket server was not started; tested form/report operations succeeded. These are recorded, not concealed.

## Regression commands and exact results

Executed in the mounted development container, bench directory `/workspace/development/frappe-bench`:

```bash
bench --site kivo-fix001-tests.localhost run-tests --module creditflow.tests.<module>
node --test apps/creditflow/creditflow/tests/test_sale_form.cjs apps/creditflow/creditflow/tests/test_report_localization.cjs
node --check <each of 25 application .js files>
```

Each module below ran separately; latest post-fix results supersede earlier exploratory failures in the newly added cash-invoice test fixture.

| Module | Passed tests |
|---|---:|
| test_billing_requests | 12 |
| test_customer_payment_workflow | 11 |
| test_french_localization | 5 |
| test_language_navigation | 2 |
| test_localization | 6 |
| test_onboarding_imports | 9 |
| test_onboarding_website | 7 |
| test_operational_reports | 3 |
| test_pilot_integrity | 9 |
| test_purchase_payment_modes | 15 |
| test_purchase_supplier_flow_regression | 15 |
| test_purchases_report | 4 |
| test_return_rounding | 3 |
| test_reversal_cash | 6 |
| test_saas_access_enforcement | 16 |
| test_sale_fiscal_invoice | 13 |
| test_sale_returns | 10 |
| test_sales_customer_flow_regression | 10 |
| test_sme_reporting | 7 |
| test_subscription_foundation | 11 |
| test_supplier_statement | 1 |
| test_verified_signup_onboarding | 25 |
| test_withholding_tej | 10 |
| test_workspace_dashboard | 10 |
| **Python integration total** | **220 / 220** |
| Sale form JS | 11 / 11 |
| Report localization JS | 2 / 2 |

JS syntax: 25 files, zero failures. Host Python AST audit parsed application Python files. Host `python3 scripts/validate_translation_catalogs.py`: all three catalogs pass. `git diff --check`: PASS. Browser scripts: `node /tmp/kivo-fr-closure/browser.cjs`, `flows.cjs`, `warnings.cjs`, successful final runs. Evidence screenshots, texts, report data, and test logs are available in `/tmp/kivo-localization-gate/evidence` on the host; credentials were excluded.

Not run: full-app/Frappe test suites (outside the relevant targeted regression scope); production checks/data operations; live Konnect checkout or paid subscription settlement (checkout is disabled on the disposable site); real email delivery; PDF generation/download and physical printing (actual HTML printview output was inspected); real-device testing beyond Chromium emulation. Paid-plan descriptions supplied by administrators are user content and require appropriate French content when configured. These limits do not replace the completed localization acceptance checks.

## Finco Tunisia comparison

Public pages checked during this closure. Finco column records advertised capabilities/terminology, not observed private UI. No overall ranking or superiority claim is made.

| Area | Kivo current verified state | Finco advertised terminology/workflow | Remaining Kivo gap |
|---|---|---|---|
| Customers/credit | Clients, encours/credit warning, soldes clients, relevé client in French; CREDIT flow submitted | Paid/partially paid/late invoices and payment follow-up ([commercial businesses](https://finco.tn/domaines/entreprises-commerciales)); account statements and reminders ([Finco](https://finco.tn/)) | No material localization gap observed; automated reminder parity not assessed |
| Sales/documents | Au comptant / À crédit / Paiement partiel, confirmation, Facture, Reçu de retour verified | Centralized devis, factures, bons de commande ([commercial management](https://finco.tn/fonctionnalites/gestion-commerciale)) | Kivo terminology is understandable; broader document-capability parity outside this gate |
| Payments/treasury | Espèces, Virement bancaire, payment receipts and customer/supplier payment flows verified | Suivi des ventes et de la trésorerie; revenues/payments in real time ([commercial management](https://finco.tn/fonctionnalites/gestion-commerciale)) | No material localization gap observed; treasury breadth not compared |
| Stock | Stock disponible, stock statuses and both filters verified; low-stock destination and movement list inspected | Stock/movement tracking, automatic inventory after receipt ([purchases/suppliers](https://finco.tn/fonctionnalites/achats-fournisseurs)) | Mobile table needs horizontal scrolling; no unverified Finco UI assumptions |
| Suppliers/purchases | Fournisseurs, Achats, Paiements fournisseurs, balances/statement/withholding print verified | Centralized suppliers, supplier documents and inventory updates ([purchases/suppliers](https://finco.tn/fonctionnalites/achats-fournisseurs)) | No material localization gap observed; supplier-document breadth not assessed |
| Reports | French balance/statement/performance/sales/purchase reports with canonical data | Rapports et Statistiques avancés and dashboards ([Finco](https://finco.tn/)) | Upstream framework wording remains; advertised report breadth cannot establish private UI parity |
| Onboarding/mobile | OWNER onboarding, signup text, 390×844 dashboard/forms/stock and English replay verified | Free account/trial, browser/mobile access and guided support ([Finco](https://finco.tn/)) | Existing grid/title truncation and framework English hints; no real-device/native-app comparison |

Inference from the observed Kivo workflows and Finco's public French vocabulary: Kivo's checked merchant language is clear enough for the same Tunisian SMB audience. This is a terminology/readability assessment, not a product equivalence claim.

## Acceptance and residual risks

All 14 localization acceptance criteria are satisfied for the tested normal workflows. No material custom Kivo English label remains in the inspected paths. Remaining framework English, technical codes, user content, mobile table scrolling, disabled external checkout branches, and browser-emulation limits are explicitly recorded above. No POS/checkout feature development was undertaken.
