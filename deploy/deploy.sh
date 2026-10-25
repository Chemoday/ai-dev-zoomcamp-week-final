#!/usr/bin/env bash
# Deploy one commit's image to one environment, with an automatic rollback on a failed health check.
#
#   deploy.sh <dev|prod> <sha> [--no-rollback]
#
# Runs as the "deploy" user from /opt/aap/releases/<sha>/deploy/ (fetched by ssh-gate.sh).
# State per environment lives in /opt/aap/<env>/: .env (secrets), current_sha, previous_sha, history.
set -euo pipefail

# shellcheck source=/dev/null
source /opt/aap/aap.conf  # AAP_ROOT, IMAGE

ENV_NAME=$1
SHA=$2
MODE=${3:-}
ENV_DIR="$AAP_ROOT/$ENV_NAME"
RELEASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# docker compose stats the working directory; never depend on where the caller happened to be.
cd "$RELEASE_DIR"

log() { echo "[deploy $ENV_NAME $(date -u +%H:%M:%S)] $*"; }

export IMAGE_REF="$IMAGE:$SHA"
export APP_ENV="$ENV_NAME"

compose() {
    docker compose -p "aap-$ENV_NAME" \
        -f "$RELEASE_DIR/docker-compose.deploy.yml" \
        --env-file "$ENV_DIR/.env" "$@"
}

pull_image() {
    if docker image inspect "$IMAGE_REF" > /dev/null 2>&1; then
        log "image $IMAGE_REF already present"
        return 0
    fi
    log "pulling $IMAGE_REF"
    if [[ -n "${GHCR_TOKEN:-}" ]]; then
        # Throwaway docker config: the short-lived CI token is never stored on the server.
        local docker_config status=0
        docker_config=$(mktemp -d)
        {
            printf '%s\n' "$GHCR_TOKEN" | DOCKER_CONFIG=$docker_config \
                docker login ghcr.io -u github-actions --password-stdin > /dev/null &&
                DOCKER_CONFIG=$docker_config docker pull --quiet "$IMAGE_REF"
        } || status=$?
        rm -rf "$docker_config"
        return "$status"
    else
        docker pull --quiet "$IMAGE_REF"
    fi
}

previous=$(cat "$ENV_DIR/current_sha" 2>/dev/null || true)

pull_image
log "starting $SHA (previous: ${previous:-none})"

# --wait blocks until every service reports healthy; the backend healthcheck hits /health/ready.
if compose up -d --remove-orphans --wait --wait-timeout 180; then
    if [[ -n "$previous" && "$previous" != "$SHA" ]]; then
        echo "$previous" > "$ENV_DIR/previous_sha"
    fi
    echo "$SHA" > "$ENV_DIR/current_sha"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $SHA" >> "$ENV_DIR/history"
    log "healthy: $ENV_NAME now runs $SHA"
    docker image prune -f --filter "until=168h" > /dev/null || true
    exit 0
fi

log "health check FAILED for $SHA; recent backend logs:"
compose logs --tail 40 backend || true

if [[ "$MODE" != "--no-rollback" && -n "$previous" && "$previous" != "$SHA" ]]; then
    log "rolling back to $previous"
    if bash "$AAP_ROOT/releases/$previous/deploy/deploy.sh" "$ENV_NAME" "$previous" --no-rollback; then
        log "rollback to $previous succeeded"
    else
        log "rollback to $previous FAILED as well; manual intervention needed"
    fi
else
    log "no previous release to roll back to"
fi
exit 1
