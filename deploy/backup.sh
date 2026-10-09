#!/bin/sh
# Run from the bench directory. Credentials are supplied through protected env/files.
set -eu
umask 077

site=${1:?Usage: sh apps/creditflow/deploy/backup.sh SITE [--local-only]}
mode=${2:-off-host}
case "$site" in ''|*[!a-zA-Z0-9.-]*) echo "Invalid site hostname" >&2; exit 2 ;; esac
case "$mode" in off-host|--local-only) ;; *) echo "Invalid backup mode" >&2; exit 2 ;; esac
[ -f "sites/$site/site_config.json" ] || { echo "Site does not exist" >&2; exit 2; }
if [ "$mode" = off-host ]; then
  : "${RESTIC_REPOSITORY:?Configure an encrypted off-host restic repository}"
  : "${RESTIC_PASSWORD_FILE:?Provide the protected restic password file}"
  [ -r "$RESTIC_PASSWORD_FILE" ] || { echo "Restic password file is unreadable" >&2; exit 2; }
  command -v restic >/dev/null
fi

# Separate generations permit atomic off-host snapshots and avoid selecting stale files.
generation=$(date -u +%Y%m%dT%H%M%SZ)-$$
destination=$(pwd)/sites/$site/private/kivo-backups/$generation
mkdir -p -m 700 "$destination"
bench --site "$site" backup --with-files --compress --ignore-backup-conf --backup-path "$destination"
site_slug=$(printf '%s' "$site" | tr . _)
for pattern in "*-$site_slug-database*.sql.gz" "*-$site_slug-site_config_backup*.json" "*-$site_slug-files*.tgz" "*-$site_slug-private-files*.tgz"; do
  count=$(find "$destination" -maxdepth 1 -type f -name "$pattern" -size +0c | wc -l)
  [ "$count" -eq 1 ] || { echo "Missing or ambiguous backup artifact: $pattern" >&2; exit 1; }
done
(
  cd "$destination"
  sha256sum ./* > SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)
if [ "$mode" = --local-only ]; then
  echo "Local backup verified; off-host replication NOT performed: $destination"
  exit 0
fi
restic backup --tag "kivo-site:$site" "$destination"
restic forget --tag "kivo-site:$site" --group-by host,tags --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
echo "Full backup replicated with encrypted restic retention: $site"
# Keep local generations until the operator's verified local retention job removes them.
# Alert on disk capacity; do not delete the sole usable backup on upload failure.
