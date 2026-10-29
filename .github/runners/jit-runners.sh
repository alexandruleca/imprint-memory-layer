#!/usr/bin/env bash
# Keeps throwaway GitHub Actions runners for this repository, each a fresh
# container that takes one job and is deleted (docs/ci-runners.md).
#
# The token that registers runners stays on the host: for every container
# this script mints a single-use just-in-time runner config, which registers
# only that runner, and passes the container nothing else. Containers get no
# Docker socket and no host mounts, so a pull request's code reaches neither
# the token nor the host.
#
# Usage: jit-runners.sh <slot-name> <platform> <labels, the only ones it gets>
#   jit-runners.sh pr-x64-1 linux/amd64 imprint-pr
#   jit-runners.sh arm64-1  linux/arm64 imprint-arm64
# Token: $GH_TOKEN, else ~/.config/imprint-runners/token, else `gh auth token`.
set -euo pipefail

SLOT="${1:?slot name}"
PLATFORM="${2:?platform, linux/amd64 or linux/arm64}"
LABELS="${3:?comma-separated custom labels}"
REPO="${IMPRINT_RUNNER_REPO:-alexandruleca/imprint-memory-layer}"
# The image the trusted runners use, which has git and build tools as hosted
# runners do; its entrypoint is bypassed, since it registers with a token.
IMAGE="${IMPRINT_RUNNER_IMAGE:-myoung34/github-runner:2.338.2-ubuntu-noble}"
CPUS="${IMPRINT_RUNNER_CPUS:-4}"
MEMORY="${IMPRINT_RUNNER_MEMORY:-8g}"

token() {
    if [ -n "${GH_TOKEN:-}" ]; then
        printf '%s' "$GH_TOKEN"
    elif [ -r "$HOME/.config/imprint-runners/token" ]; then
        tr -d '[:space:]' < "$HOME/.config/imprint-runners/token"
    else
        gh auth token
    fi
}

# jitconfig mints the config for one runner named after the slot. A runner
# left registered by a killed container keeps its name, so it is removed first.
jitconfig() {
    local name="bedrock-$SLOT" stale
    stale=$(GH_TOKEN="$(token)" gh api "repos/$REPO/actions/runners?per_page=100" \
        --jq ".runners[] | select(.name == \"$name\") | .id")
    for id in $stale; do
        GH_TOKEN="$(token)" gh api -X DELETE "repos/$REPO/actions/runners/$id" >/dev/null || true
    done
    # Only the custom labels: GitHub matches a job's labels as a subset, so
    # without self-hosted/Linux/X64 no trusted job lands in a PR container.
    local labels
    labels=$(printf '"%s",' ${LABELS//,/ })
    GH_TOKEN="$(token)" gh api -X POST "repos/$REPO/actions/runners/generate-jitconfig" \
        --input - --jq .encoded_jit_config <<EOF
{"name": "$name", "runner_group_id": 1, "labels": [${labels%,}], "work_folder": "_work"}
EOF
}

trap 'docker rm -f "imprint-jit-$SLOT" >/dev/null 2>&1 || true; exit 0' TERM INT

delay=5
while true; do
    if config=$(jitconfig); then
        delay=5
        # --init reaps the job's leftover processes; the container and all it
        # wrote go away when its one job ends.
        docker run --rm --init --name "imprint-jit-$SLOT" \
            --platform "$PLATFORM" \
            --cpus "$CPUS" --memory "$MEMORY" --pids-limit 4096 \
            -e RUNNER_ALLOW_RUNASROOT=1 --workdir /actions-runner \
            --entrypoint ./run.sh "$IMAGE" --jitconfig "$config" || true
    else
        echo "jit-runners[$SLOT]: minting a runner config failed; retrying in ${delay}s" >&2
        sleep "$delay"
        delay=$(( delay < 300 ? delay * 2 : 300 ))
    fi
done
