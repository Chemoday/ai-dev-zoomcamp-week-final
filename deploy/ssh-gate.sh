#!/usr/bin/env bash
# SSH forced command for the CI deploy key (installed by bootstrap.sh as /opt/aap/bin/ssh-gate).
#
# The key cannot open a shell; it can only run:
#   deploy <dev|prod> <40-char commit sha>
#   status
# For "deploy", an optional GHCR token is read from the first line of stdin.
set -euo pipefail

# shellcheck source=/dev/null
source /opt/aap/aap.conf  # AAP_ROOT, REPO, IMAGE

fail() { echo "ssh-gate: $*" >&2; exit 2; }

read -r -a args <<< "${SSH_ORIGINAL_COMMAND:-}"

github_api() {
    curl -fsSL --retry 3 -H "Accept: application/vnd.github+json" "https://api.github.com/repos/${REPO}/$1"
}

# Forks share the object store with the parent repo, so "the SHA exists" is not enough: a commit
# must be on main (dev, prod) or be the head of an open same-repo pull request (dev only).
on_main() {
    github_api "compare/main...$1" \
        | python3 -c 'import json,sys; sys.exit(json.load(sys.stdin)["status"] not in ("identical", "behind"))'
}

open_pr_head() {
    local sha=$1
    github_api "commits/${sha}/pulls" | python3 -c '
import json, sys
sha, repo = sys.argv[1], sys.argv[2]
ok = any(
    pr["state"] == "open" and pr["head"]["sha"] == sha and pr["head"]["repo"]["full_name"] == repo
    for pr in json.load(sys.stdin)
)
sys.exit(not ok)' "$sha" "$REPO"
}

fetch_release() {
    # Download deploy/ of that exact commit, so config and image always come from the same SHA.
    local sha=$1 release_dir="$AAP_ROOT/releases/$1"
    [[ -f "$release_dir/deploy/deploy.sh" ]] && return 0
    local tmp
    tmp=$(mktemp -d "$AAP_ROOT/releases/.incoming.XXXXXX")
    if ! curl -fsSL --retry 3 "https://codeload.github.com/${REPO}/tar.gz/${sha}" \
        | tar -xz -C "$tmp" --strip-components=1 --wildcards '*/deploy/*'; then
        rm -rf "$tmp"
        fail "cannot download deploy/ for commit $sha"
    fi
    mv "$tmp" "$release_dir"
}

case "${args[0]:-}" in
    deploy)
        [[ ${#args[@]} -eq 3 ]] || fail "usage: deploy <dev|prod> <sha>"
        env=${args[1]} sha=${args[2]}
        [[ "$env" =~ ^(dev|prod)$ ]] || fail "unknown environment: $env"
        [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "sha must be a full 40-char commit hash"
        if [[ "$env" == prod ]]; then
            on_main "$sha" || fail "prod only runs commits on main; $sha is not on main"
            grep -q " ${sha}$" "$AAP_ROOT/dev/history" 2>/dev/null \
                || fail "commit $sha was never deployed to dev; promote only what dev has run"
        else
            on_main "$sha" || open_pr_head "$sha" \
                || fail "commit $sha is neither on main nor an open PR head of $REPO"
        fi
        GHCR_TOKEN=""
        if [[ ! -t 0 ]]; then
            IFS= read -r -t 5 GHCR_TOKEN || true
        fi
        export GHCR_TOKEN
        fetch_release "$sha"
        exec bash "$AAP_ROOT/releases/$sha/deploy/deploy.sh" "$env" "$sha"
        ;;
    status)
        for env in dev prod; do
            printf '%s current=%s previous=%s\n' "$env" \
                "$(cat "$AAP_ROOT/$env/current_sha" 2>/dev/null || echo none)" \
                "$(cat "$AAP_ROOT/$env/previous_sha" 2>/dev/null || echo none)"
        done
        ;;
    *)
        fail "command not allowed: ${SSH_ORIGINAL_COMMAND:-<empty>}"
        ;;
esac
