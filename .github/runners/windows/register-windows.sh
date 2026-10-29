#!/usr/bin/env bash
# Drops a fresh runner registration token into each Windows VM's shared
# folder; oem/install.ps1 in the VM waits for it, registers the runner and
# deletes it (docs/ci-runners.md). A token expires an hour after it is
# minted, so each is minted only once the VM's install.ps1 reports, with
# shared/<vm>/waiting-for-token, that it is waiting: this can run right
# after `docker compose up`.
#
# A VM that has taken its token has Windows installed on its disk, so this
# then marks the VM installed. dockur writes that marker itself only on a
# clean container stop; without it, a crash or power loss makes the next
# start wipe the disk and install Windows again. The installation media is
# deleted too, as dockur would.
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
    if [ ! -e "$shared/waiting-for-token" ]; then
        echo "Waiting for $name to finish setting up (shared/$name/install.log)..."
        while [ ! -e "$shared/waiting-for-token" ]; do sleep 15; done
    fi
    token=$(gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token)
    printf '%s' "https://github.com/$REPO" > "$shared/runner-url"
    printf '%s' "bedrock-$name" > "$shared/runner-name"
    printf '%s' runner > "$shared/runner-user"
    (umask 077; printf '%s' "$IMPRINT_WINDOWS_PASSWORD" > "$shared/runner-password"
     printf '%s' "$token" > "$shared/runner.token")
    echo "Token for bedrock-$name is in $shared; waiting for the VM to take it."
done

for name in "$@"; do
    deadline=$(( $(date +%s) + 3600 ))
    while [ -e "$HERE/shared/$name/runner.token" ]; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            echo "bedrock-$name did not take its token within the hour it is valid; run this again" >&2
            exit 1
        fi
        sleep 15
    done
    docker exec "imprint-$name" sh -c 'touch /storage/windows.boot && rm -f /storage/*.iso'
    echo "bedrock-$name took its token; marked installed, installation media deleted."
done
