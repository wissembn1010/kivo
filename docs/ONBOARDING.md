# CreditFlow first SME onboarding

## Fresh Bench development or pilot site

```sh
bench new-site <site-name>
bench --site <site-name> install-app creditflow
bench --site <site-name> migrate
bench --site <site-name> clear-cache
```

Sign in as Administrator, create the first **Business**, create or select the first User, assign the **OWNER** role, and set **CreditFlow Business** on that User. The OWNER can then complete the legal name, address, phone, email, matricule fiscal, and optional commercial registration on the Business form. STAFF can read but cannot edit these settings.

Open **CreditFlow Import** in Desk. Download the template for each import, prepare the CSV, run Preview, fix every row error, then Commit. Recommended order:

1. Customers
2. Suppliers
3. Products (base UOM only)
4. Opening stock
5. Opening customer debt
6. Opening supplier debt

Opening imports create submitted append-only ledger documents. Exact-file re-imports are blocked. Keep the source CSV and returned import-batch identifier with the pilot records.

## Development and verification

```sh
bench --site <site-name> migrate
bench --site <site-name> run-tests --app creditflow
git -C apps/creditflow diff --check
```

## Upgrade

```sh
bench --site <site-name> backup --with-files --compress
git -C apps/creditflow pull --ff-only
bench --site <site-name> migrate
bench --site <site-name> clear-cache
bench restart
```

Use the deployment platform's supported restart command when not using a conventional Bench process manager.

## Backup and restore

Create a full backup:

```sh
bench --site <site-name> backup --with-files --compress
```

Restore into a separately created recovery site, never over the only production copy:

```sh
bench --site <recovery-site> restore <database.sql.gz> --with-public-files <files.tgz> --with-private-files <private-files.tgz>
bench --site <recovery-site> migrate
bench --site <recovery-site> clear-cache
```

Preserve the encrypted site configuration and encryption key securely. See `docs/PRODUCTION.md` and `docs/DEPLOYMENT.md` for the full production runbook.

## CSV behavior and limitations

Imports are all-or-nothing: Commit is rejected if any row is invalid. Customer and Supplier names must be unique within the Business for import purposes; Product reference/SKU follows the current Product uniqueness rule. Product imports accept only a base UOM already installed and active. Configure alternate-UOM conversions on the Product afterward.
