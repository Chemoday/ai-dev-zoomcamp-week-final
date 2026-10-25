#!/usr/bin/env bash
# One-time, idempotent VPS setup. Run as root from a copy of the deploy/ directory:
#
#   PUBLIC_IP=164.68.121.228 HTTPS_PORT=443 DEPLOY_PUBKEY_FILE=/tmp/ci_deploy.pub bash bootstrap.sh
#
# Creates the "deploy" user (CI key pinned to ssh-gate), /opt/aap layout, per-environment
# database secrets, the shared "aap-edge" Docker network and the Caddy edge proxy.
# Re-running it is safe: existing secrets, deploy state and certificates are kept.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${PUBLIC_IP:?PUBLIC_IP is required}"
: "${DEPLOY_PUBKEY_FILE:?DEPLOY_PUBKEY_FILE is required}"
HTTPS_PORT=${HTTPS_PORT:-443}
REPO=${REPO:-Chemoday/ai-dev-zoomcamp-week-final}
AAP_ROOT=/opt/aap
DASHED_IP=${PUBLIC_IP//./-}
PROD_HOST="${DASHED_IP}.sslip.io"
DEV_HOST="dev.${DASHED_IP}.sslip.io"
IMAGE="ghcr.io/${REPO,,}"

log() { echo "[bootstrap] $*"; }

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
for tool in docker curl python3 openssl; do
    command -v "$tool" > /dev/null || { echo "missing dependency: $tool" >&2; exit 1; }
done

log "deploy user"
id deploy > /dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
usermod -aG docker deploy

log "directory layout in $AAP_ROOT"
install -d -o root -g root -m 755 "$AAP_ROOT" "$AAP_ROOT/bin" "$AAP_ROOT/edge"
install -d -o deploy -g deploy -m 750 "$AAP_ROOT/dev" "$AAP_ROOT/prod" "$AAP_ROOT/releases"

cat > "$AAP_ROOT/aap.conf" <<EOF
AAP_ROOT=$AAP_ROOT
REPO=$REPO
IMAGE=$IMAGE
EOF
chmod 644 "$AAP_ROOT/aap.conf"

install -o root -g root -m 755 "$HERE/ssh-gate.sh" "$AAP_ROOT/bin/ssh-gate"

log "CI deploy key (forced command, no shell, no forwarding)"
install -d -o root -g root -m 755 /home/deploy/.ssh
{
    printf 'restrict,command="%s/bin/ssh-gate" ' "$AAP_ROOT"
    cat "$DEPLOY_PUBKEY_FILE"
} > /home/deploy/.ssh/authorized_keys
chown root:root /home/deploy/.ssh/authorized_keys
chmod 644 /home/deploy/.ssh/authorized_keys

for env_name in dev prod; do
    env_file="$AAP_ROOT/$env_name/.env"
    if [[ ! -f "$env_file" ]]; then
        log "generating database secret for $env_name"
        printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)" > "$env_file"
    fi
    chown deploy:deploy "$env_file"
    chmod 600 "$env_file"
done

log "docker network aap-edge"
docker network inspect aap-edge > /dev/null 2>&1 || docker network create aap-edge > /dev/null

log "edge proxy: https://$PROD_HOST:$HTTPS_PORT (prod), https://$DEV_HOST:$HTTPS_PORT (dev)"
install -d -o root -g root -m 755 "$AAP_ROOT/edge/caddy"
install -m 644 "$HERE/edge/docker-compose.yml" "$AAP_ROOT/edge/"
install -m 644 "$HERE/edge/Caddyfile" "$AAP_ROOT/edge/caddy/Caddyfile"
rm -f "$AAP_ROOT/edge/Caddyfile"  # pre-directory-mount layout
cat > "$AAP_ROOT/edge/.env" <<EOF
HTTPS_PORT=$HTTPS_PORT
PROD_HOST=$PROD_HOST
DEV_HOST=$DEV_HOST
EOF
edge_compose() {
    docker compose -p aap-edge -f "$AAP_ROOT/edge/docker-compose.yml" \
        --env-file "$AAP_ROOT/edge/.env" "$@"
}
edge_compose up -d --remove-orphans
# A Caddyfile change does not recreate the container, so reload explicitly (Caddy may still be
# starting, hence the retries; the last error is shown if every attempt fails).
reloaded=false
for _ in $(seq 1 15); do
    if reload_output=$(edge_compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>&1); then
        reloaded=true
        break
    fi
    sleep 1
done
$reloaded || { echo "caddy reload failed: $reload_output" >&2; exit 1; }

log "done"
