# Deployment, CD & Rollback

The app runs on a single self-hosted VPS (`164.68.121.228`, Ubuntu 24.04, Docker) next to
unrelated services. HTTPS uses free `sslip.io` hostnames, which resolve to the IP embedded in the
name, so no domain had to be bought. Caddy obtains Let's Encrypt certificates for them on its own.

| Environment | URL | Deployed by |
| :--- | :--- | :--- |
| **dev** | https://dev.164-68-121-228.sslip.io:8443 | Automatically, on every push to `main` that passes CI |
| **prod** | https://164-68-121-228.sslip.io:8443 | Manually, by promoting a commit that already ran on dev |

> **Port 8443 is temporary.** Port `443` on the VPS is still used by an Outline VPN server. Once
> it moves, the switch to `443` is a re-run of `bootstrap.sh` with `HTTPS_PORT=443` plus an update
> of the `DEV_URL` / `PROD_URL` repository variables. Certificates are issued over port `80`
> (HTTP-01), so HTTPS works on either port.

## Topology

```text
                 Internet
                    │  :80 (ACME + redirect)   :8443 → later :443 (HTTPS)
          ┌─────────▼──────────┐
          │ aap-edge: Caddy    │  the only project container that binds host ports
          └───┬────────────┬───┘
   docker net │ aap-edge   │
   ┌──────────▼───┐   ┌────▼─────────┐
   │ aap-prod     │   │ aap-dev      │   one Compose project per environment
   │  backend ────┤   │  backend ────┤   (alias aap-<env>-backend on aap-edge)
   │  postgres    │   │  postgres    │   own internal network + volume, never published
   └──────────────┘   └──────────────┘
```

Server layout (`/opt/aap`):

| Path | Owner | Contents |
| :--- | :--- | :--- |
| `aap.conf`, `bin/ssh-gate`, `edge/` | root | Installed by `bootstrap.sh`; the CI key cannot change them |
| `dev/`, `prod/` | deploy | `.env` (database secret, mode 600), `current_sha`, `previous_sha`, `history` |
| `releases/<sha>/deploy/` | deploy | The `deploy/` directory of each deployed commit |

## Pipeline

```text
pull request ──► CI: ruff + pytest (PostgreSQL service), frontend lint + test + build ──► image ghcr.io/chemoday/ai-dev-zoomcamp-week-final:<head sha>
   label deploy-dev ──► Deploy (dev, <head sha>) ──► smoke test via the public URL
push to main ──► CI: ruff + pytest ──► image :<sha> and :main ──► Deploy (dev) ──► smoke test via the public URL
manual       ──► Deploy (prod, <sha>) ──► smoke test via the public URL
```

- **`.github/workflows/ci.yml`** runs on every pull request and every push to `main`. Images are
  built once per commit and tagged with its SHA; servers never build. The image contains the
  client: its first stage builds `frontend/` with bun (an empty `frontend/` gives an API-only
  image).
- **Pull request preview.** A same-repository pull request with the label `deploy-dev` is deployed
  to dev after CI, on every push to it. The Frontend sync pull request carries the label, so each
  frontend version shows up on dev before the merge (docs/FRONTEND_GIT_WORKFLOW.md). Dev shows
  whatever was deployed last; the next merge to `main` replaces a preview. Do not preview a pull
  request that changes database migrations.
- **`.github/workflows/deploy.yml`** is reused by CI for dev and is the manual entry point for prod
  and rollbacks: *Actions → Deploy → Run workflow*, pick `prod`, paste the full commit SHA.
  The `production` environment only accepts runs from `main`.
- The job connects as `deploy@164.68.121.228`. That key is pinned to a forced command
  (`deploy/ssh-gate.sh`), so all it can run is `deploy <dev|prod> <sha>` or `status`. It cannot get
  a shell or set up port forwarding.
- The gate accepts a commit for **dev** when it is on `main` or is the head of an open same-repo
  pull request. It accepts a commit for **prod** only when it is on `main` *and* is listed in dev's
  history. It then downloads `deploy/` of that exact commit and runs its `deploy.sh`, so the config
  and the image always come from the same SHA.
- The short-lived `GITHUB_TOKEN` used to pull the image goes over stdin into a throwaway Docker
  config and is never written to the server.

## Demo data

Both environments run with `DEMO_MODE_ENABLED=true` and `SEED_DEMO_DATA=true`
(`deploy/docker-compose.deploy.yml`). At startup, after the migrations, `entrypoint.sh` runs
`python -m src.core.seed`: it seeds the demo building into an **empty** database once and does
nothing on every later start, or when the database already has a building. To start the demo
over on dev, the dev database has to be emptied; prod is never reset by a deploy.

## Rollback

- **Automatic.** `deploy.sh` starts the new release with `docker compose up --wait`. The backend
  healthcheck calls `/health/ready`, which also checks the database. If the release is not healthy
  within 180 s, the script prints the backend logs, redeploys the previous SHA of that environment
  and fails the workflow run.
- **Manual.** Run *Deploy* with an older SHA. The SHAs that ran are listed on the repository's
  *Deployments* page, or with `ssh deploy@164.68.121.228 status`.
- **Schema.** Migrations must stay backward compatible (expand/contract). At startup,
  `python -m src.core.migrations` upgrades the database. When the database is already *newer* than
  the image (a rollback), it logs `migrations_skipped_schema_ahead` and skips the upgrade instead
  of crashing.

## One-time setup

1. **VPS** (as root, from a copy of `deploy/`):
   ```bash
   PUBLIC_IP=164.68.121.228 HTTPS_PORT=8443 DEPLOY_PUBKEY_FILE=./ci_deploy.pub bash bootstrap.sh
   ```
   This creates the `deploy` user and the `/opt/aap` layout, generates a random database password
   per environment, creates the `aap-edge` network and starts Caddy. Re-running it is safe:
   secrets, deploy state and certificates are kept.
2. **GitHub repository settings**:
   - Secrets: `DEPLOY_SSH_KEY` (private half of the CI key) and `DEPLOY_KNOWN_HOSTS` (the VPS
     ed25519 host key, verified against `/etc/ssh/ssh_host_ed25519_key.pub`).
   - Variables: `DEPLOY_HOST`, `DEV_URL`, `PROD_URL`, `FRONTEND_REPOSITORY` (frontend sync).
   - Secret `FRONTEND_SYNC_TOKEN`: a token that reads the frontend repository and writes this
     one (pushes and pull requests of the Frontend sync workflow must start CI).
   - Environments: `dev`, and `production` restricted to the `main` branch.

## Notes

- `DEMO_MODE_ENABLED=true` in both environments, so reviewers can switch roles with
  `X-Demo-User-Id`. The deployed databases contain seeded demo data only.
- Troubleshooting (as root on the VPS):
  `docker compose -p aap-dev logs -f backend`, `cat /opt/aap/dev/history`,
  `docker logs aap-edge-caddy-1`.
