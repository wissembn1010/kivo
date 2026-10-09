# Kivo production deployment and recovery

This supplements DEPLOYMENT.md with the B-13/B-15 execution procedure. Use an
isolated VPS/staging target before production. The application baseline is
`34c0ac96aa2041d748aa11a352744914634a4b51` (P0 remediation), Frappe `16.30.0`
at `9523516cac25992bc2cd810e1015df8994c257f5`, and frappe_docker
`4546724524c140bd4b76549b5a724527b78b123c`. Operational files added after that
baseline must themselves be reviewed/committed before building the next image.
Do not deploy a dirty checkout or assume version `0.0.1` identifies a release.

## Source, image and production topology

The configured origin is `https://github.com/wissembn1010/kivo.git`. At this
inspection its `kivo-taj-integration` branch resolves to the pre-P0 commit
`9d9429b67bd10edc0e474f6cadaabba1173a7818`. The local `v0.1.0-rc1` tag is
older still. Publishing an authorized immutable release ref and retaining the
built image ID/digest are prerequisites; no push/tag was performed here.

Follow DEPLOYMENT.md for clean-host Docker installation and immutable-image
build. Kivo/Frappe SHA assertions in deploy/Containerfile deliberately reject
the wrong Git revision. Debian apt and Python/Node transitive dependencies are
not completely locked: retain/promote the built image, rather than assuming a
later rebuild produces identical bytes. Credentials belong in protected
BuildKit secrets, not Git URLs in tracked files or image layers.

Copy `deploy/compose.operations.yaml` alongside the pinned override. Render
using a protected environment whose SITE_NAME/DOMAIN are the final hostname:

```sh
cd /opt/creditflow/frappe_docker
docker compose --env-file /opt/creditflow/.env.production \
  -f compose.yaml -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml -f overrides/compose.https.yaml \
  -f /opt/creditflow/compose.pins.yaml \
  -f /opt/creditflow/compose.operations.yaml \
  config > /opt/creditflow/compose.production.yaml
chmod 600 /opt/creditflow/compose.production.yaml
```

The rendered stack must include frontend, backend, websocket, scheduler,
queue-short, queue-long, redis-cache, redis-queue, db, proxy and the one-shot
configurator. Only proxy ports 80/443 are published. Named volumes retain sites,
database, Redis queue, logs and certificate state. Container stdout logs rotate
at 10 MB x 3 per service; Frappe file logs use its rotating handlers. Configure
host disk monitoring and central log retention separately. The development
container's broad workspace bind mount and development database bootstrap
password must never be copied to production. The Traefik read-only Docker
socket mount still grants Docker API visibility; restrict host access and
consider a restricted socket proxy on the actual host.

## DNS, HTTPS and forwarded headers

Required DNS, without making any live changes:

```text
A     <DOMAIN>                <VPS_IPV4>
AAAA  <DOMAIN>                <VPS_IPV6>  (only if IPv6 routing/firewall works)
CNAME www.<DOMAIN>            <DOMAIN>    (only if deliberately routed)
```

Kivo's Business tenants share one Frappe site, so wildcard tenant DNS is not
required. Additional Frappe sites require explicit site directories and router
Host rules. Use SITE_NAME=<DOMAIN>, SITES_RULE='Host(`<DOMAIN>`)' and
FRAPPE_SITE_NAME_HEADER=$host. Set both `host_name` and
`creditflow_public_base_url` to `https://<DOMAIN>`; do not reuse a trycloudflare
hostname. Direct path: client TLS -> Traefik :443 -> frontend Nginx :8080 ->
backend :8000 or Socket.IO :9000. The supplied HTTPS override redirects :80 to
:443 and uses ACME HTTP-01 with persisted cert-data; request real certificates
only after target DNS/firewall approval.

If Cloudflare proxying is selected: client TLS terminates at Cloudflare, a
second validated TLS connection terminates at Traefik, and traffic then follows
the same internal path. Use Full (strict), not Flexible. For a named Tunnel,
document its actual Service URL and certificate verification separately; the
development quick tunnel is not a production certificate/routing test.

Nginx forwards Host, original scheme and WebSocket Upgrade headers. Configure
UPSTREAM_REAL_IP_ADDRESS with the actual trusted Traefik address/CIDR and
UPSTREAM_REAL_IP_RECURSIVE=on after inspecting the target Docker network.
The upstream loopback default does not trust a separate Traefik container.
Do not trust 0.0.0.0/0. If Cloudflare is used, Traefik must trust only the
maintained Cloudflare proxy ranges before accepting forwarded headers; verify
client IP/rate limiting through the entire path. Do not expose frontend directly.

## Safe release update and migration

Build dependencies and assets into the new image before the maintenance window;
do not run bench update/git pull inside the immutable production image. Stage
the exact image with a restored backup first. In the commands below, `dc` is:

```sh
dc() { docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml "$@"; }
```

1. Verify free space, a full off-host backup and recoverable keys. Record the old
   image digest/configuration. Announce the maintenance window.
2. Stop scheduling; let already running jobs finish. Stop workers with an
   adequate graceful timeout. Maintenance mode prevents new web writes:

   ```sh
   dc exec -T backend bench --site "$SITE_NAME" set-maintenance-mode on
   dc exec -T backend bench --site "$SITE_NAME" disable-scheduler
   dc stop -t 300 scheduler queue-short queue-long
   dc exec -T backend bench --site "$SITE_NAME" backup --with-files --compress --ignore-backup-conf
   dc stop -t 120 backend websocket frontend
   ```

3. Load/pull the reviewed image; change CUSTOM_TAG and re-render the Compose
   file. Run migrations in a one-off new-image backend before serving new code:

   ```sh
   dc run --rm --no-deps backend bench --site "$SITE_NAME" migrate
   dc run --rm --no-deps backend bench --site "$SITE_NAME" clear-cache
   dc up -d backend websocket frontend
   ```

   If migration fails, keep maintenance enabled and workers stopped. Diagnose
   first; restore the pre-update DB/files/config into an isolated site if
   reverting. Switching to an older image alone is not a database rollback.

4. Confirm schema/Patch Log, health and login. Production site settings must
   override developer_mode=0, allow_tests=false, live_reload=false,
   serve_default_site=false and the canonical HTTPS origins. Preserve the
   existing site encryption key; never replace it during a routine upgrade.
5. After checks, leave maintenance and enable background work:

   ```sh
   dc exec -T backend bench --site "$SITE_NAME" set-maintenance-mode off
   dc exec -T backend bench --site "$SITE_NAME" enable-scheduler
   dc exec -T backend bench --site "$SITE_NAME" set-config pause_scheduler false
   dc up -d scheduler queue-short queue-long
   dc exec -T backend bench doctor
   dc exec -T backend bench --site "$SITE_NAME" scheduler status
   ```

The P0 opening-key patch runs post-model-sync. Its nullable unique field permits
existing audit rows; historical duplicates retain their postings and one audit
row reserves each economic key. The dedicated restore drill verified the patch
record, index and preserved opening balances. Corrupt audit references fail
closed and need investigation, not deletion of customer postings.

## Health, restart and SMTP acceptance

No new public health endpoint is needed: Frappe's existing
`/api/method/ping` and `/login` are sufficient HTTP probes. For each target:

```sh
curl --fail --silent --show-error "https://$SITE_NAME/api/method/ping"
curl --fail --silent --show-error -o /dev/null "https://$SITE_NAME/login"
dc ps
dc exec -T backend bench doctor
dc exec -T backend bench --site "$SITE_NAME" scheduler status
dc exec -T redis-cache redis-cli ping
dc exec -T redis-queue redis-cli ping
curl --fail --silent --show-error "https://$SITE_NAME/socket.io/?EIO=4&transport=polling"
```

Confirm a browser WebSocket upgrade through HTTPS, actual OWNER login/own-Business
request, guest denial for a known private-file probe, DB SELECT 1, current
migration record and no pending patches. Worker/scheduler process health is not
proof of job completion: enqueue a harmless `frappe.utils.now` job on each of
short/default/long and verify finished status. Inspect Failed Job/RQ FailedJobRegistry,
Background Jobs, Scheduled Job Log and protected worker logs; alert on stalled
queues, failed jobs and missing scheduler heartbeats. Do not enqueue every
scheduled business/email task merely to test readiness.

Enable Docker at host boot. Service policies are unless-stopped; configurator is
on-failure. Explicitly stopped services remain stopped on daemon/host restart.
Compose marks unhealthy containers but does not automatically restart a running
unhealthy process. Test individual worker/proxy restarts, Docker restart and a
controlled host reboot on staging only; provide external alerting/remediation.

APPLICATION READY for transactional signup/reset/verification paths does not
certify delivery. EXTERNAL SMTP CONFIGURATION REQUIRED: provision host, port,
sender, credentials and exactly the provider's STARTTLS or implicit-SSL mode.
The development Email Account has both TLS and SSL flags; inspect/correct the
production account rather than copying it. Required DNS placeholders:

```text
TXT <MAIL_DOMAIN>                       "v=spf1 <PROVIDER_AUTHORIZED_MECHANISM> -all"
TXT <SELECTOR>._domainkey.<MAIL_DOMAIN>  "<PROVIDER_DKIM_PUBLIC_KEY_RECORD>"
TXT _dmarc.<MAIL_DOMAIN>                "v=DMARC1; p=<REVIEWED_POLICY>; rua=mailto:<DMARC_REPORT_ADDRESS>"
```

Do not invent SPF includes, DKIM selectors/keys, SMTP values or DMARC policy.
Validate delivery to an approved recipient only during authorized production
acceptance; no real mail was sent during this audit.

## Automated full backups and off-host retention

`deploy/backup.sh` creates four artifacts plus SHA256SUMS with restrictive modes.
It deliberately overrides partial-backup excludes/includes and verifies all
artifact names. Local generations use `private/kivo-backups`, outside Frappe's
flat `private/backups` directory (v16 backup discovery cannot handle generation
subdirectories there). It supports Frappe's optional encrypted filename suffixes.

```sh
# Test only: this does NOT satisfy off-host protection.
sh apps/creditflow/deploy/backup.sh "$SITE_NAME" --local-only
# Production: fail closed if restic configuration is missing.
sh apps/creditflow/deploy/backup.sh "$SITE_NAME"
```

The production image includes restic. Provision an independent off-host/object
storage repository and initialize it once, with RESTIC_REPOSITORY and
RESTIC_PASSWORD_FILE configured outside Git. Mount its protected password file
read-only at `/run/kivo-backup/password`, readable by container UID 1000; keep
object-storage credentials in protected environment/secret-manager injection.
Use separate storage credentials with least privilege and independent retention
or object lock to resist deletion by a compromised production host. Back up the
restic password separately from the repository.

The helper encrypts restic snapshots and retains 14 daily, 8 weekly and 12 monthly
snapshots per site. Confirm policy against legal requirements/RPO/RTO. Schedule
it at least daily (six-hourly if the agreed RPO requires it) with a host systemd
timer/cron or external job runner that calls:

```sh
docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml \
  exec -T -e RESTIC_REPOSITORY -e RESTIC_PASSWORD_FILE \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
  backend sh apps/creditflow/deploy/backup.sh "$SITE_NAME"
```

For S3, these variables come from a protected host EnvironmentFile. Other
backends require their own credentials. Monitor exit status and the age of the
last successful remote snapshot; nonzero must alert externally. Run `restic
check` and isolated recovery drills regularly. Local generations are retained
until an operator verifies remote snapshots and removes old local copies;
monitor disk usage and define that local cleanup policy. Do not remove the last
usable local backup on upload failure. The upstream backup-cron override is not
included in the production render and its default command omits file archives;
do not treat it as this full/off-host policy.

## Fresh-server recovery, exact sequence

1. Provision a clean, isolated host with DNS/traffic initially disabled. Install
   Docker/Compose using DEPLOYMENT.md; enable Docker at boot and restrict ports.
2. Clone the pinned frappe_docker revision and reviewed Kivo release. Load the
   retained custom-image archive/digest, or build the exact code release; never
   restore a newer DB into an older application image.
3. Restore protected `.env.production`, Compose overrides, restic repository
   credentials/password and certificate state from independent secure storage.
   Keep live domain/SMTP/integration egress disabled on a drill clone.
4. Fetch the matching DB dump, public archive, private archive and config backup
   from the same restic snapshot. Verify SHA256SUMS before using any artifact.
5. Render Compose as above. Start db, redis-cache, redis-queue and configurator.
   Do not start workers/scheduler or route user traffic yet.
6. Create a distinctly named empty recovery site; enter DB root/Administrator
   credentials interactively, not in shell history:

   ```sh
   dc run --rm --no-deps backend bench new-site "$RECOVERY_SITE" --install-app creditflow
   dc run --rm --no-deps backend bench --site "$RECOVERY_SITE" restore \
     /restore/database.sql.gz --with-public-files /restore/files.tgz \
     --with-private-files /restore/private-files.tgz
   ```

   Mount the restore directory read-only into backend in a protected override.
   For Frappe-encrypted backups restore the backup decryption key securely and
   use the matching encrypted artifacts. Do not use --force to conceal a
   downgrade or validation failure.
7. Reapply the original **site encryption_key** securely, preserving the newly
   created destination DB credentials/name. Use a protected config merge or
   stdin/secret-manager session; do not paste encryption keys on command lines.
   Also preserve the distinct backup_encryption_key if enabled. Copy required
   site-specific subscription/billing/email configuration only when intentionally
   activating the real target; a drill must keep these integrations disabled.
8. Set pause_scheduler=true, mute_emails=true, maintenance_mode=true on the clone;
   disable copied Email Accounts and external integrations before serving it.
   Set destination host_name and creditflow_public_base_url appropriately.
9. Run migrate and clear-cache on the restored site. List apps; confirm Frappe and
   Kivo, core DocTypes, P0 Patch Log and the economic-key unique index. Assets are
   already built into the retained image; if rebuilding, complete bench build
   before acceptance.
10. Start backend/frontend/websocket. Temporarily disable maintenance only for
    restricted local/staging smoke tests. Verify login, OWNER own-Business API,
    public/private file checksums and guest private-file denial. Validate encrypted
    Password-field decryption without displaying values.
11. Compare source/restore Business/master/stock/ledger counts and aggregate
    balances from the same backup point. Run focused smoke tests with synthetic
    accounts; never run destructive app tests on restored real customer data.
12. For actual disaster recovery, approve final canonical DNS/TLS/SMTP/integration
    settings; resume scheduler/workers and run health/queued-job checks. Confirm
    only one restored copy is writable and original production is offline.
13. Record recovery duration, backup timestamp, RPO/RTO and sign-off. Keep the
    old backup/image until validation completes. A temporary clone may be removed
    only after confirming its exact identity and absence of unique user data.

Separately escrow site encryption_key, optional backup_encryption_key, restic
password, DB/root bootstrap credentials, Administrator break-glass credentials,
SMTP/API/billing secrets, private Git/registry access and TLS/ACME material.
Site configuration alone does not capture common_site_config.json or host
deployment secrets; retain the reviewed topology and secure host configuration
as well. Never store actual values in this repository or the release report.
