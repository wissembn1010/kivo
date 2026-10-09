# CreditFlow production backup and recovery

Run these commands from the Frappe bench directory. Replace every value in angle
brackets. Never paste real passwords, database credentials, or encryption keys
into this document, source control, shell scripts, or tickets.

For the exact Docker fresh-server procedure, health checks, tested backup helper
and off-host retention policy, use [RELEASE_OPERATIONS.md](RELEASE_OPERATIONS.md).

## A. Create a full backup

```sh
cd <frappe-bench-path>
bench --site <site-name> backup --with-files --compress
```

A successful run prints four paths: the site configuration backup, compressed
database dump, public-files archive, and private-files archive. Copy all four to
encrypted off-server storage and apply a retention policy. Treat every backup as
confidential production data.

## B. Locate backup files

By default Frappe writes backups below:

```text
sites/<site-name>/private/backups/
```

List names and sizes without displaying contents:

```sh
find sites/<site-name>/private/backups -maxdepth 1 -type f -printf '%f %s bytes\n'
```

## C. Restore the database

Never test a restore over the only production site. Provision a separate site
and database first. Ensure the same compatible Frappe and CreditFlow code is
checked out and available in the bench.

Create the recovery site; Bench prompts for secrets when they are required:

```sh
bench new-site <recovery-site-name> --install-app creditflow
```

Restore the database and both file archives:

```sh
bench --site <recovery-site-name> restore \
  <database.sql.gz> \
  --with-public-files <files.tgz> \
  --with-private-files <private-files.tgz>
```

If the database server requires administrative credentials, provide
`--db-root-username` and allow Bench to prompt for the password. Avoid putting
passwords directly on the command line.

## D. Restore site configuration and encryption material

The `site_config_backup.json` file is sensitive. Keep it encrypted and outside
Git. Reapply required site-specific settings through the supported configuration
process. In particular, the original encryption key is required to decrypt
stored Password fields:

```sh
RECOVERY_SITE=<recovery-site-name> env/bin/python - <<'PY'
import json, os
from pathlib import Path
source = json.loads(Path('/restore/site_config_backup.json').read_text())
target = Path('sites') / os.environ['RECOVERY_SITE'] / 'site_config.json'
destination = json.loads(target.read_text())
for key in ('encryption_key', 'backup_encryption_key'):
    if source.get(key):
        destination[key] = source[key]
destination.update(pause_scheduler=True, mute_emails=True, maintenance_mode=True)
target.write_text(json.dumps(destination, indent=2))
target.chmod(0o600)
PY
```

Run this from a protected administrative session with the config backup mounted
read-only. It transfers key material without printing it or placing it in command
arguments, and preserves the destination database credentials. Disable copied
Email Accounts/external integrations on a drill before serving it.

## E. Migrate after restore

```sh
bench --site <recovery-site-name> migrate
bench --site <recovery-site-name> clear-cache
```

Confirm that `creditflow` appears in the installed-app list:

```sh
bench --site <recovery-site-name> list-apps
```

## F. Restart services

For a production Bench managed by Supervisor:

```sh
sudo supervisorctl restart all
```

For another process manager or container platform, restart the Frappe web,
worker, scheduler, and realtime services using that platform's documented
procedure. Do not use development-mode `bench start` as a production service
manager.

## G. Verify CreditFlow after recovery

1. Sign in as Administrator and confirm Business, Customer, Product, and Supplier
   records open.
2. Confirm the OWNER and STAFF roles exist and User has the
   `creditflow_business` Link field.
3. Open Customer Balances and Stock On Hand and compare sample totals with
   submitted Credit Transactions and Stock Movements.
4. Confirm OWNER/STAFF users only see their assigned Business.
5. Run authenticated login/OWNER, file-checksum, encryption and stock/ledger smoke
   checks from RELEASE_OPERATIONS.md. Run the full app suite only on a separate
   disposable synthetic test site; it contains destructive/committing tests and
   must not be run against recovered customer data.

6. Verify expected baseline counts and perform a stock reconciliation before
   accepting the recovery.
7. Keep the original site offline or read-only until recovery verification is
   signed off.

After a temporary drill, remove it only after verifying the exact site name:

```sh
bench drop-site <recovery-site-name>
```

Bench creates another backup by default before dropping a site. Use
`--no-backup` only for a disposable drill site whose verified backup already
exists.
