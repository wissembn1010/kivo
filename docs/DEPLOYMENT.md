# CreditFlow Docker production deployment

This runbook prepares a standard single-VPS deployment using the official
`frappe/frappe_docker` production Compose stack with a custom immutable image.
Do not use `pwd.yml`: it is a disposable demo and cannot reliably carry custom
apps. Replace every value in angle brackets. Commands are run on the VPS unless
stated otherwise.

CreditFlow currently targets Frappe `version-16`. Pin the Frappe Docker commit,
CreditFlow release tag, and image tag used for each release.

## 1. Prepare a fresh Ubuntu or Debian VPS

Before starting, create DNS records only during the real deployment window:

```text
A  <DOMAIN>  ->  <SERVER_IP>
```

Connect using a sudo-capable account, update packages, and install prerequisites:

```sh
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y ca-certificates curl git gnupg
sudo timedatectl set-timezone UTC
```

Enable the VPS firewall after confirming SSH access:

```sh
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Do not expose MariaDB or Redis ports publicly.

## 2. Install and verify Docker

Use Docker's official apt repository for the VPS distribution:

```sh
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg |
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" |
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
sudo docker version
sudo docker compose version
```

For Debian, replace `ubuntu` in the repository URL with `debian`. Keep Docker
and the OS patched.

## 3. Copy the deployment sources

A production deployment must use a reviewed, committed CreditFlow release:

```sh
sudo mkdir -p /opt/creditflow
sudo chown "$USER":"$USER" /opt/creditflow
cd /opt/creditflow

git clone https://github.com/frappe/frappe_docker.git
cd frappe_docker
git checkout <PINNED_FRAPPE_DOCKER_COMMIT>
```

Do not copy the development site's database, logs, `site_config.json`, private
files, or migration source files into Git.

Create `apps.json` in this deployment directory. For a public repository:

```json
[
  {
    "url": "<CREDITFLOW_GIT_URL>",
    "branch": "<CREDITFLOW_RELEASE_BRANCH_OR_TAG>"
  }
]
```

Keep `apps.json` outside the CreditFlow repository. For a private repository,
use the official BuildKit secret/private-repository mechanism; never embed a
token in the URL, Dockerfile, image layer, or Git.

## 4. Configure production environment variables

Copy `.env.production.example` from the CreditFlow release to a protected
deployment location:

```sh
install -m 600 /path/to/creditflow/.env.production.example /opt/creditflow/.env.production
editor /opt/creditflow/.env.production
```

Replace every placeholder. Generate high-entropy MariaDB root,
Administrator, and encryption secrets. Store them in a password manager or
secret manager. The real `.env.production` is Git-ignored.

Load only non-secret build variables when practical:

```sh
cd /opt/creditflow/frappe_docker
set -a
. /opt/creditflow/.env.production
set +a
```

## 5. Build the immutable CreditFlow image

Build the custom/layered image with CreditFlow included at build time:

```sh
docker buildx build \
  --build-arg FRAPPE_BRANCH="$FRAPPE_BRANCH" \
  --secret id=apps_json,src=apps.json \
  --tag "$CUSTOM_IMAGE:$CUSTOM_TAG" \
  --file images/layered/Containerfile \
  .
```

Generate a production Compose file with MariaDB, Redis, and Traefik HTTPS:

```sh
docker compose --env-file /opt/creditflow/.env.production \
  -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.https.yaml \
  config > /opt/creditflow/compose.production.yaml
chmod 600 /opt/creditflow/compose.production.yaml
```

Review the rendered file without publishing it. Confirm backend, frontend,
websocket, scheduler, short/long workers, Redis cache/queue, MariaDB, and reverse
proxy services exist.

## 6. Start services and create the production site

Start the production stack:

```sh
docker compose --project-name creditflow \
  --env-file /opt/creditflow/.env.production \
  -f /opt/creditflow/compose.production.yaml up -d
docker compose --project-name creditflow \
  -f /opt/creditflow/compose.production.yaml ps
```

Create the site interactively so passwords are not saved in shell history:

```sh
docker compose --project-name creditflow \
  -f /opt/creditflow/compose.production.yaml exec backend \
  bench new-site "$SITE_NAME" \
  --mariadb-user-host-login-scope='172.%.%.%' \
  --install-app creditflow
```

Bench prompts for the MariaDB root and Administrator passwords. Confirm the app:

```sh
docker compose --project-name creditflow \
  -f /opt/creditflow/compose.production.yaml exec backend \
  bench --site "$SITE_NAME" list-apps
```

## 7. Migrate and configure production mode

```sh
docker compose --project-name creditflow \
  -f /opt/creditflow/compose.production.yaml exec backend \
  bench --site "$SITE_NAME" migrate

docker compose --project-name creditflow \
  -f /opt/creditflow/compose.production.yaml exec backend \
  bench --site "$SITE_NAME" set-config developer_mode 0

docker compose --project-name creditflow \
  -f /opt/creditflow/compose.production.yaml exec backend \
  bench --site "$SITE_NAME" set-config allow_tests false
```

Do not enable developer mode, test mode, or `allow_tests` in production.
Run tests on staging before promoting an image.

## 8. Configure the domain and HTTPS

Use the public domain as `SITE_NAME`. Set Frappe's canonical URL:

```sh
docker compose --project-name creditflow \
  -f /opt/creditflow/compose.production.yaml exec backend \
  bench --site "$SITE_NAME" set-config host_name "https://$DOMAIN"
```

Set `SITES_RULE=Host(\`<DOMAIN>\`)` and
`LETSENCRYPT_EMAIL=<ADMIN_EMAIL>` in the protected environment file. Only after
DNS resolves to `SERVER_IP` and ports 80/443 are reachable, recreate the stack
so Traefik can request the real certificate:

```sh
docker compose --project-name creditflow \
  --env-file /opt/creditflow/.env.production \
  -f /opt/creditflow/compose.production.yaml up -d
```

Do not request certificates or change DNS during preparation.

## 9. Start, restart, and inspect services

```sh
docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml ps
docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml restart
docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml logs --tail=200 backend frontend websocket scheduler queue-short queue-long
```

Do not publish logs containing customer information. Configure rotation and
restricted access.

## 10. Persistence and production configuration audit

The Compose deployment must retain named volumes for:

- MariaDB data;
- Frappe `sites`, including public/private files and site configuration;
- logs where the selected Compose stack declares them;
- Traefik certificate state;
- backup output, followed by encrypted off-server replication.

Verify volumes and mounts before accepting the site:

```sh
docker volume ls
docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml config
```

Do not run MariaDB, Redis, workers, scheduler, or Socket.IO as ad-hoc host
processes. Redis cache/queue and the websocket service are required. The frontend
reverse proxy must route HTTP/WebSocket traffic and serve built assets from the
same immutable release.

## 11. Create OWNER and STAFF users

Sign in as Administrator:

1. Create or open a User.
2. For an OWNER, assign the `OWNER` role and set `CreditFlow Business`.
3. For a STAFF user, assign the `STAFF` role and set
   `CreditFlow Business`.
4. Assign exactly one Business per CreditFlow user.
5. Test each account after saving.

A normal OWNER/STAFF user without `creditflow_business` receives no protected
CreditFlow data and cannot create protected documents. Administrator and System
Manager retain their administrative bypass.

## 12. Health and acceptance checklist

- [ ] DNS resolves `DOMAIN` to `SERVER_IP`.
- [ ] HTTPS is valid and HTTP redirects to HTTPS.
- [ ] Administrator login works and its initial password was rotated.
- [ ] `creditflow` is listed by `bench --site "$SITE_NAME" list-apps`.
- [ ] All containers are healthy; scheduler and both worker queues remain running.
- [ ] Websocket requests connect without proxy errors.
- [ ] Static assets load without browser console errors.
- [ ] The repository-backed CreditFlow Workspace loads with its shortcuts, reports, and five business-scoped KPI cards.
- [ ] OWNER sees only its assigned Business and can use master, operational, and
      reversal workflows.
- [ ] STAFF sees only its assigned Business, can post Purchase/Sale/Payment, and
      cannot create ledger/movement records or reversals manually.
- [ ] Another Business's records are inaccessible by list, Link search, report,
      and direct URL.
- [ ] A clearly named production acceptance Purchase posts stock.
- [ ] A clearly named production acceptance Sale reduces stock and increases
      Customer Balance.
- [ ] A partial Payment reduces Customer Balance.
- [ ] Customer Outstanding Balance matches submitted Credit Transactions.
- [ ] Customer Balances report is correct to three decimals.
- [ ] Stock On Hand report matches submitted Stock Movements.
- [ ] Append-only cancellation/reversal rules still hold.
- [ ] A full backup including public/private files succeeds and is copied
      off-server.
- [ ] A restore drill succeeds on a separate recovery site.

Remove or reverse acceptance documents using supported workflows; never edit or
delete submitted accounting/stock records.

## 13. First production backup and restore

Create and copy a full backup immediately after acceptance:

```sh
docker compose --project-name creditflow \
  -f /opt/creditflow/compose.production.yaml exec backend \
  bench --site "$SITE_NAME" backup --with-files --compress
```

Use [PRODUCTION.md](PRODUCTION.md) for database/public/private-file restoration,
encryption-key handling, migration, verification, and isolated restore drills.
Backups stored only inside the Docker volume are not sufficient; replicate them
encrypted to a separate system.

## 14. Future release update procedure

1. Take and verify a full off-server backup.
2. Tag the reviewed CreditFlow release and build a new immutable image tag.
3. Test that exact image and a restored production backup on staging.
4. Pull or load the new image on the VPS.
5. Update only `CUSTOM_TAG` in the protected environment.
6. Regenerate `compose.production.yaml` from the pinned Frappe Docker files.
7. Recreate services, migrate once, clear cache, and verify health:

```sh
docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml up -d
docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml exec backend bench --site "$SITE_NAME" migrate
docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml exec backend bench --site "$SITE_NAME" clear-cache
docker compose --project-name creditflow -f /opt/creditflow/compose.production.yaml ps
```

8. Run the health checklist.
9. Keep the previous immutable image and pre-update backup available for the
   tested recovery procedure. Do not downgrade the database casually.
