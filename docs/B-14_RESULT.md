ID: B-14

Status: PASS

Staging schema status: Synchronized on creditflow-staging.localhost through normal bench migrate; no hand-created tables or manual schema patches. CreditFlow Plan, Plan Entitlement, Subscription and Pending Signup tables verified after tests. Installed app schemas, standard fixtures and all five Kivo post-sync patches completed successfully. Migration log: /tmp/kivo_b14_staging_migrate.log. Preflight confirmed a separate database from development/test sites, no ordinary users, and only RC1 Staging business/transaction fixtures. Backup with database, config and public/private files completed before migration: sites/creditflow-staging.localhost/private/backups/20260910_011825-creditflow-staging_localhost-*.

Pilot plan: User-approved STARTER, enabled, monthly/annual price 0 TND. Staging creditflow_default_trial_plan=STARTER. Default signup trial remains 14 days; no code change or new billing feature.

Entitlements: STARTER has exactly migration / BOOLEAN / true, applied through the normal Frappe Document save API. No other optional entitlements configured; BUSINESS and PRO have none. Final readback verified this after all tests. Actual STARTER trial import succeeded; switching only the synthetic trial to BUSINESS or PRO denied import. Unknown/other optional features and expired-trial import denied. Existing legacy no-subscription compatibility remains unchanged; the claim concerns newly signed-up pilot trials.

Checkout: Staging creditflow_paid_checkout_enabled=0 explicitly persisted using bench set-config. Direct service and actual RPC checkout denied for STARTER, BUSINESS and PRO, including injected request opt-in. Zero provider calls and zero payment inserts in acceptance probe. Subscription page rendered 200 with Starter/TRIAL and no checkout buttons. Existing 22-test coverage also verifies expired page state and continued reconciliation of prior payments; paid billing blockers were not redesigned or enabled.

Signup/trial verification: Actual request_signup -> captured verification token -> verify_and_provision created a synthetic OWNER/Business and STARTER TRIAL with exactly 14 days using staging's real configuration, without overriding its plan/entitlements/checkout settings. Entitled import worked only within that Business. Synthetic acceptance transaction rolled back, User absence checked afterward; final subscription count 0. Verification email was captured, not delivered; this is application-path acceptance, not live SMTP/network certification.

Focused tests: 22/22 passed on staging (all original 21 plus rendered-page/API acceptance regression). Additional actual-configuration probe passed signup, plan/entitlements, import, expiry, page and RPC restrictions. Log: /tmp/kivo_b14_staging_focus.log. No application/test source changes in this configuration-only continuation. kivo-fix/security guided staging-only normal lifecycle changes; kivo-test guided scoped validation.

Related tests: 102/102 passed on staging: subscription foundation 11, SaaS access 16, imports 6, verified signup 25, stored Business authorization 19, child authorization 18, shared UOM 7. Log: /tmp/kivo_b14_staging_related.log. CI=1 applied to test processes only; no persistent allow_tests change. git diff --check passed. Full suite not rerun for this configuration-only task; prior documented result remains 419 total / 409 passed / 5 baseline failures / 5 baseline errors in /tmp/kivo_b14_accept_full.log. No targeted regressions.

Migration required: YES — completed on the approved staging site using bench --site creditflow-staging.localhost migrate. No reinstallation, reset, manual table patch or migration bypass. Backup available above.

Data modified: Staging schema/reference fixtures, standard legacy RC1 fixture backfills, STARTER entitlement/pricing and the two explicit staging configuration keys; synthetic test fixtures only. Original staging transaction counts/totals unchanged across migration and acceptance probe: Sale 1/7.035, Purchase 1/12.345, Payment 1/2.000, Credit Transaction 4/18.070, Stock Movement 4/26.000 quantity. NO production/customer data or development.localhost configuration/data mutated. Source report updated: apps/creditflow/docs/B-14_RESULT.md.

Remaining issue: None for B-14's approved unpaid staging pilot scope. This does not certify external production serving, live email or deferred paid billing. No other Class A work performed.

Remaining Class A count: 9.
