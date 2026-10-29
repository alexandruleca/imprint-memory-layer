# shellcheck shell=bash disable=SC2034 # settings for the scripts that source this
# Shared settings for the disposable Apple silicon runner (ADR 0115).
# Sourced by prepare-image.sh, supervise.sh and install.sh; every value can be
# overridden from the environment.

REPO="${IMPRINT_VM_REPO:-alexandruleca/imprint-memory-layer}"
# Pinned by digest: Cirrus Labs publishes only `latest`.
BASE_IMAGE="${IMPRINT_VM_BASE_IMAGE:-ghcr.io/cirruslabs/macos-tahoe-base@sha256:4aaa751fa7538902d73c767f27a066fc87e83f183069da4e4b5022863abd4c77}"
GOLDEN_VM="${IMPRINT_VM_GOLDEN:-imprint-runner-golden}"
VM_PREFIX="${IMPRINT_VM_PREFIX:-imprint-runner-job}"
VM_CPU="${IMPRINT_VM_CPU:-6}"
VM_MEMORY_MB="${IMPRINT_VM_MEMORY_MB:-8192}"
VM_DISK_GB="${IMPRINT_VM_DISK_GB:-100}"
RUNNER_LABELS="${IMPRINT_VM_LABELS:-self-hosted,macOS,ARM64,imprint-apple-silicon}"
# The label that marks a queued job as ours.
RUNNER_MATCH_LABEL="${IMPRINT_VM_MATCH_LABEL:-imprint-apple-silicon}"
RUNNER_NAME_PREFIX="${IMPRINT_VM_RUNNER_PREFIX:-$(scutil --get LocalHostName 2>/dev/null || hostname -s)-vm}"
# Guest user of the Cirrus Labs images; its runner lives in its home.
GUEST_RUNNER_DIR="${IMPRINT_VM_GUEST_RUNNER_DIR:-/Users/admin/actions-runner}"
# Optional keychain item holding a fine-grained token (Administration: write on
# REPO). Without it, the host's `gh` login is used.
KEYCHAIN_SERVICE="${IMPRINT_VM_KEYCHAIN_SERVICE:-imprint-vm-runner}"

TART="${TART:-$(command -v tart || echo "$HOME/.local/bin/tart")}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >&2; }

github_token() {
  security find-generic-password -s "$KEYCHAIN_SERVICE" -w 2>/dev/null || gh auth token
}

# Wait until the guest agent answers, or fail after $2 seconds.
wait_for_guest() {
  local vm=$1 deadline=$((SECONDS + ${2:-300}))
  until "$TART" exec "$vm" /usr/bin/true >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      log "guest agent in $vm did not answer"
      return 1
    fi
    sleep 3
  done
}
