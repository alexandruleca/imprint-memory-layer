#!/usr/bin/env bash
# Drops a fresh runner registration token into each Windows VM's shared
# folder; oem/install.ps1 in the VM waits for it, registers the runner and
# deletes it (docs/ci-runners.md). Run it once the VMs have finished
# installing Windows: a token expires an hour after it is minted.
#
# Usage: register-windows.sh [win1 win2 ...]
# Needs: gh logged in with admin rights on the repository, and
#        IMPRINT_WINDOWS_PASSWORD (or .env beside compose.yml).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${IMPRINT_RUNNER_REPO:-alexandruleca/imprint-memory-layer}"
[ -f "$HERE/.env" ] && . "$HERE/.env"
: "${IMPRINT_WINDOWS_PASSWORD:?set it in $HERE/.env}"

[ $# -gt 0 ] || set -- win1 win2
for name in "$@"; do
    shared="$HERE/shared/$name"
    mkdir -p "$shared"
    token=$(gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token)
    printf '%s' "https://github.com/$REPO" > "$shared/runner-url"
    printf '%s' "bedrock-$name" > "$shared/runner-name"
    printf '%s' runner > "$shared/runner-user"
    (umask 077; printf '%s' "$IMPRINT_WINDOWS_PASSWORD" > "$shared/runner-password"
     printf '%s' "$token" > "$shared/runner.token")
    echo "Token for bedrock-$name is in $shared; the VM registers within a minute."
done
