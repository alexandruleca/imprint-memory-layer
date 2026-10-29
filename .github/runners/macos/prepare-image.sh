#!/usr/bin/env bash
# Bake the golden VM that every disposable runner VM is cloned from (ADR 0115):
# the pinned Cirrus Labs macOS base plus the build prerequisites and a
# checksum-verified GitHub Actions runner. Re-run it to pick up a newer runner;
# the previous golden VM is replaced only after the new one is complete.
set -euo pipefail
# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

work_vm="$GOLDEN_VM-new"
download=$(mktemp -d)
cleanup() {
  "$TART" stop "$work_vm" >/dev/null 2>&1 || true
  rm -rf "$download"
}
trap cleanup EXIT

log "pulling $BASE_IMAGE"
"$TART" pull "$BASE_IMAGE"
"$TART" delete "$work_vm" >/dev/null 2>&1 || true
"$TART" clone "$BASE_IMAGE" "$work_vm"
"$TART" set "$work_vm" --cpu "$VM_CPU" --memory "$VM_MEMORY_MB" --disk-size "$VM_DISK_GB"

"$TART" run --no-graphics --no-audio "$work_vm" >/dev/null 2>&1 &
vm_pid=$!
wait_for_guest "$work_vm"

guest() { "$TART" exec "$work_vm" /bin/bash -c "$1"; }

log "growing the guest APFS container to the disk"
# The guest agent usually grows it at boot already; then diskutil reports that
# the size is unchanged.
guest 'yes | sudo diskutil repairDisk disk0 >/dev/null; sudo diskutil apfs resizeContainer disk0s2 0 >/dev/null 2>&1; df -h /'

log "installing build prerequisites"
# shellcheck disable=SC2016 # expands in the guest
guest 'eval "$(/opt/homebrew/bin/brew shellenv)"
  export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
  brew update --quiet
  brew install --quiet cmake librsvg python@3.12
  brew cleanup --prune=all
  xcode-select -p
  python3.12 --version; cmake --version | head -1; rsvg-convert --version'

log "fetching the latest GitHub Actions runner"
release=$(gh api repos/actions/runner/releases/latest)
version=$(jq -r .tag_name <<<"$release")
version=${version#v}
asset="actions-runner-osx-arm64-$version.tar.gz"
# The release notes carry each archive's SHA256 between marker comments.
expected=$(jq -r .body <<<"$release" | sed -n 's/.*<!-- BEGIN SHA osx-arm64 -->\([0-9a-f]\{64\}\)<!-- END SHA osx-arm64 -->.*/\1/p')
test -n "$expected"
gh release download "v$version" -R actions/runner -p "$asset" -D "$download"
echo "$expected  $download/$asset" | shasum -a 256 -c -

log "installing runner $version in the guest"
guest "rm -rf '$GUEST_RUNNER_DIR' && mkdir -p '$GUEST_RUNNER_DIR'"
"$TART" exec -i "$work_vm" /usr/bin/tar -xzf - -C "$GUEST_RUNNER_DIR" <"$download/$asset"
# The runner reads .path and .env from its directory for every job.
# IMPRINT_DISPOSABLE_RUNNER is what lets a job install packages system-wide.
guest "cd '$GUEST_RUNNER_DIR'
  printf '%s\n' '/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin' >.path
  printf '%s\n' 'LANG=en_US.UTF-8' 'IMPRINT_DISPOSABLE_RUNNER=1' >.env
  grep -qx IMPRINT_DISPOSABLE_RUNNER=1 .env
  ./bin/Runner.Listener --version"

# Writes the guest has not flushed are lost when `tart stop` ends the VM: an
# image stopped seconds after this step kept the base image's runner and had no
# .env. Flush, shut the guest down and wait for the VM to exit.
log "shutting the guest down"
guest 'sync; sudo /sbin/shutdown -h now' || true
for _ in $(seq 1 60); do
  kill -0 "$vm_pid" 2>/dev/null || break
  sleep 2
done
if kill -0 "$vm_pid" 2>/dev/null; then
  log "the guest did not shut down"
  exit 1
fi
"$TART" delete "$GOLDEN_VM" >/dev/null 2>&1 || true
"$TART" rename "$work_vm" "$GOLDEN_VM"
log "golden VM $GOLDEN_VM ready with runner $version"
