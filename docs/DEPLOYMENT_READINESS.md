# Kivo deployment readiness

Assessment: 17 September 2026. Repository preparation only; no image build, Compose render, migration, deployment, B-13 target check or B-15 operations drill was performed. The tested application baseline is `f96c5e0402e370078819eed6d2c1dbc6044d6fac` (461 tests: 451 passed, five documented failures, five documented errors; no new regression). This deployment-only child commit changes no ERP/business source, so the application test suite was not rerun. P0 and code-side Class A remain zero.

## Release identity and inputs

| Input | Pin |
|---|---|
| Kivo application baseline | `f96c5e0402e370078819eed6d2c1dbc6044d6fac` |
| Deployment release | Full child-commit SHA; set `KIVO_RELEASE_SHA` and `CUSTOM_TAG` to it before build |
| Frappe | `v16.30.0` / `9523516cac25992bc2cd810e1015df8994c257f5` |
| Frappe Docker | `4546724524c140bd4b76549b5a724527b78b123c` |
| Python / Node / Bench | 3.14.2 / 24.13.0 / 5.31.0, matching the tested bench |

Official Docker Hub tag metadata and OCI registry manifest HEAD responses agreed on these multi-platform digests for the existing version lines:

| Existing tag | Immutable manifest digest |
|---|---|
| `python:3.14.2-slim-bookworm` | `sha256:e87711ef5c86aaeaa7031718a69db79d334d94c545c709583f651b8185870941` |
| `mariadb:11.8` | `sha256:8b5f33ebd85d1775657e974ed10434128bb493c80e826ceaa54074fd1a92a112` |
| `redis:8.6-alpine` | `sha256:75934ddb37bfaebe3b4082ba673cac39f66495244134f33dd0a502ce03cdcd36` |
| `traefik:v3.6` | `sha256:31267173a15b4944e797a76ffd9c419707c8d8b32fe5b610f80cd0cfa05f372d` |

The release `deploy/Containerfile` uses the pinned Python image and checks the exact Frappe and Kivo Git SHAs before removing Git metadata. The release Compose override requires the pinned MariaDB, Redis and Traefik images. Node and Bench versions are explicit. Apt, npm and other upstream package downloads are not completely locked; retain and test the resulting image by immutable ID/digest.

## Fresh VPS installation sequence

1. Obtain an authorized private Kivo Git URL and a lightweight tag at the deployment release commit. Verify the tag resolves to `KIVO_RELEASE_SHA`. No remote or tag is configured here; nothing was pushed.
2. Check out the exact Frappe Docker commit. Copy the deployment-only files from the release to protected paths; keep `apps.json` and the real environment file outside Git. Replace environment placeholders with target values and set `KIVO_RELEASE_SHA`/`CUSTOM_TAG` to the release commit.
3. Build with `deploy/Containerfile` and the protected BuildKit `apps_json` secret. `bench init` installs Frappe first, then CreditFlow. Verify both source SHAs during build, export the image with `--load`, and record its image ID.
4. Render Compose from `compose.yaml`, MariaDB/Redis/HTTPS overrides and `deploy/compose.pins.yaml`. Create the production site, install `creditflow`, run normal migrations, verify assets, and set production flags and unpaid-pilot configuration.
5. Configure the domain, HTTPS/Traefik, SMTP, protected database/admin/encryption credentials, persistent volumes, backups, monitoring, health checks and restart handling. Then perform B-13/B-15 target acceptance before admitting external pilot users or real trading data.

The selected Compose stack defines backend, frontend, two workers, scheduler, websocket, MariaDB, Redis and Traefik. It retains sites, database, Redis queue and certificate volumes. Only MariaDB has a built-in health check; target probes and log/backup retention still need configuration. The MariaDB override has an unsafe `123` fallback if `DB_PASSWORD` is omitted, so the protected environment must set it explicitly. The example `ADMIN_PASSWORD`, `ENCRYPTION_KEY` and `MAIL_*` fields are not consumed directly by Compose and require separate Frappe site setup. Never copy local site data, logs, databases or credentials.

## Current verdict

The repository deployment files are ready for a deployment-only commit. A fresh VPS cannot deploy this release yet: Kivo has no authorized remote/tag, Docker is unavailable here for image build or Compose validation, and the target configuration is not supplied. No release tag should be created until the image digests above are retained in the committed deployment files. B-13 and B-15 remain operational gates; the known print/navigation test failures require target acceptance and have not been waived.
