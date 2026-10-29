#!/usr/bin/env bash
# Sets up this Linux host's share of the repository's runners
# (docs/ci-runners.md):
#   - qemu-user binfmt handlers, so arm64 containers run here (sudo, once)
#   - lingering for this user, so the runner units run without a login
#   - Docker waiting for the VM disk and stopping the VMs cleanly (sudo, once)
#   - throwaway runner slots as systemd user units: PR jobs on x64, and
#     arm64 jobs under emulation
#   - the Windows VMs' .env with a generated password, if missing
# The trusted x64 runners stay the Docker Swarm stack `imprint-runner`.
#
# Slots: IMPRINT_PR_SLOTS (default 2) x64 PR runners, IMPRINT_ARM64_SLOTS
# (default 2) arm64 runners.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/imprint-runners"
UNITS="$HOME/.config/systemd/user"

if [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
    echo "Installing qemu-user binfmt handlers for arm64 (sudo)..."
    sudo apt-get install -y qemu-user-static binfmt-support
fi
loginctl show-user "$USER" --property=Linger | grep -q yes || sudo loginctl enable-linger "$USER"
# Docker waits for the disk that holds the Windows VMs: /mnt/internal is
# mounted nofail, and started without it, the VMs would install afresh onto
# the root disk. A shutdown gives the VMs their stop_grace_period of 2
# minutes; systemd's default of 90 seconds would kill Windows mid-shutdown.
DROPIN=/etc/systemd/system/docker.service.d/imprint-runners.conf
if [ ! -f "$DROPIN" ]; then
    echo "Making Docker wait for ${IMPRINT_VM_DIR:-/mnt/internal/vm} and stop slowly enough for the VMs (sudo)..."
    sudo install -Dm644 /dev/stdin "$DROPIN" <<EOF
[Unit]
RequiresMountsFor=${IMPRINT_VM_DIR:-/mnt/internal/vm}

[Service]
TimeoutStopSec=180
EOF
    sudo systemctl daemon-reload
fi

gh auth status >/dev/null 2>&1 || [ -r "$HOME/.config/imprint-runners/token" ] || {
    echo "Log in with gh auth login, or put a token in ~/.config/imprint-runners/token" >&2
    exit 1
}

install -Dm755 "$HERE/jit-runners.sh" "$DEST/jit-runners.sh"
install -Dm644 "$HERE/imprint-jit-runner@.service" "$UNITS/imprint-jit-runner@.service"
docker pull --quiet --platform linux/amd64 myoung34/github-runner:2.338.2-ubuntu-noble >/dev/null
docker pull --quiet --platform linux/arm64 myoung34/github-runner:2.338.2-ubuntu-noble >/dev/null
systemctl --user daemon-reload

for i in $(seq "${IMPRINT_PR_SLOTS:-2}"); do
    systemctl --user enable --now "imprint-jit-runner@pr-x64-$i:linux-amd64:imprint-pr.service"
done
for i in $(seq "${IMPRINT_ARM64_SLOTS:-2}"); do
    systemctl --user enable --now "imprint-jit-runner@arm64-$i:linux-arm64:imprint-arm64.service"
done

if [ ! -f "$HERE/windows/.env" ]; then
    (umask 077; printf 'IMPRINT_WINDOWS_PASSWORD=%s\n' "$(head -c 18 /dev/urandom | base64 | tr -d '/+=')" > "$HERE/windows/.env")
    echo "Wrote $HERE/windows/.env with a generated Windows password"
fi
echo "Runner slots: systemctl --user list-units 'imprint-jit-runner@*'"
echo "Windows VMs:  docker compose -f $HERE/windows/compose.yml up -d, then windows/register-windows.sh"
