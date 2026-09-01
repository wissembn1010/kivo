# CreditFlow production readiness runbook

## Required configuration

Set secrets in the private site configuration or environment-backed Frappe configuration, never in Git:

- `creditflow_konnect_api_key`
- `creditflow_konnect_receiver_wallet_id`
- `creditflow_konnect_sandbox` (`0` in production)
- `creditflow_public_base_url` (`https://<production-domain>`)

Run the secret-safe diagnostic:

```bash
bench --site <site> execute creditflow.billing.readiness.get_konnect_readiness
```

Production also requires `developer_mode: 0`, live reload disabled, a production MariaDB service and credentials, Redis cache/queue/socketio services, web workers, background workers, scheduler, HTTPS reverse proxy, trusted production hostname, secure SMTP, log rotation, and restricted ownership/modes for `sites/<site>/site_config.json` and private files. Preserve Frappe's CSRF/session defaults; terminate HTTP at HTTPS and forward the original scheme/host correctly.

## Deploy

```bash
bench --site <site> backup --with-files --compress
bench --site <site> migrate
bench build --app creditflow
bench --site <site> clear-cache
bench restart
```

Default UOM and plan seed patches are existence-checked and idempotent. They do not create subscriptions for existing Businesses.

## Backup policy

- Automated encrypted off-host database plus public/private file backup at least daily.
- Retain daily backups for 14 days, weekly backups for 8 weeks, and monthly backups according to legal/business policy.
- Store `site_config.json`/secrets separately in an encrypted secrets system; restrict restore access.
- Monitor job success and periodically test restoration into a disposable site.

Manual backup:

```bash
bench --site <site> backup --with-files --compress
```

Disposable restore drill (never target production or the source site):

```bash
bench new-site <restore-test-site> --db-root-password '<from-secret-store>' --admin-password '<temporary-secret>'
bench --site <restore-test-site> install-app creditflow
bench --site <restore-test-site> restore <database.sql.gz> --with-public-files <public-files.tgz> --with-private-files <private-files.tgz>
bench --site <restore-test-site> migrate
bench --site <restore-test-site> run-tests --app creditflow
```

Validate document counts, subscriptions, ledgers, stock, reports, login, and file downloads, then securely remove the disposable site under the deployment team's approved procedure.

## Production smoke checklist

- [ ] Signup page loads over HTTPS
- [ ] Pending signup is created without a tenant
- [ ] Verification email and single-use link work
- [ ] Password setup works
- [ ] Business and OWNER assignment are correct
- [ ] 14-day STARTER trial exists
- [ ] Business onboarding completes
- [ ] Product, Customer, and Supplier creation works
- [ ] CREDIT and PAID Sales reconcile
- [ ] Customer Payment reconciles
- [ ] CREDIT, PAID, and PARTIAL Purchases reconcile
- [ ] Supplier Payment reconciles
- [ ] Stock On Hand reconciles
- [ ] Customer and Supplier Statements reconcile
- [ ] Sales and Purchases Reports reconcile
- [ ] Migration CSV succeeds and remains idempotent
- [ ] Tenant isolation attacks fail
- [ ] Expired trial is read-only
- [ ] ACTIVE subscription restores writes
- [ ] OWNER initiates hosted checkout; STAFF cannot
- [ ] Konnect webhook/status reconciliation confirms payment
- [ ] Billing history records pending/success/failure safely

## Monitoring

Alert on HTTP 5xx rates, failed verification email delivery, long-lived PENDING Billing Payments, Konnect timeouts/401/429/5xx responses, webhook and reconciliation errors, failed background jobs, worker/scheduler outages, MariaDB/Redis health, database and filesystem capacity, certificate expiry, and backup/restore-drill failures. Logs must never contain API keys, passwords, card data, or full provider credentials.
