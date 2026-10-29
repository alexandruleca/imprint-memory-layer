---
title: CI runners
description: Where each GitHub Actions job runs and how to set up and maintain the self-hosted runners.
---

Every workflow job runs on the owner's machines ([ADR 0112](adr/0112-ci-runs-on-the-owners-machines.md)). The files are in [`.github/runners/`](../.github/runners/).

| Label set | Runs | Where | Jobs |
| --- | --- | --- | --- |
| `self-hosted, linux, x64` | trusted only | bedrock, Swarm stack `imprint-runner`: 5 ephemeral containers with the Docker socket, host network | Linux jobs of pushes, releases, manual and scheduled runs |
| `imprint-pr` | pull requests | bedrock, throwaway containers from `jit-runners.sh` (2 slots) | Linux x64 jobs of `ci.yml` on pull requests |
| `imprint-arm64` | both | bedrock, throwaway arm64 containers under qemu-user (1 slot) | Linux arm64 jobs |
| `self-hosted, windows, x64, imprint-windows` | trusted only | bedrock, Windows 11 VMs `win1` and `win2` (dockur/windows on KVM) | Windows jobs |
| `self-hosted, macOS, ARM64, imprint-apple-silicon` | trusted only | the Apple silicon laptop, a disposable Tart VM per job ([ADR 0115](adr/0115-apple-silicon-jobs-run-in-disposable-tart-vms.md)) | macOS jobs |

Pull requests run no Windows or macOS jobs. The Windows VMs persist between jobs, and the macOS VMs, though discarded after each job, can reach the laptop's network, so PR code never reaches them.

## Security

The repository is public. A pull request's workflow files decide what its jobs run, and a fork can name any label, including the trusted ones. Labels are no boundary:

- The throwaway runners (`imprint-pr`, `imprint-arm64`) are the only ones built for untrusted code. Each container takes one job with a single-use just-in-time config. It gets no token, no Docker socket and no host mounts, and it is deleted afterwards.
- The trusted Swarm runners hold an admin token in their environment and mount the Docker socket, which is root on bedrock. The Windows VMs keep state between jobs. The macOS VMs are deleted after each job but sit on the laptop's NAT network, with access to the laptop and the LAN.
- So set **Settings → Actions → General → Approval for running fork pull request workflows** to *Require approval for all external contributors*. Before approving, read the PR's changes to `.github/`: a PR that edits a workflow can target the trusted runners.

## Setting up bedrock

1. **Throwaway runners and arm64 emulation.** This installs `qemu-user-static` (sudo), enables lingering, and starts the systemd user units `imprint-jit-runner@…`:
   ```bash
   .github/runners/install-host.sh
   ```
   The supervisor mints runner configs with `$GH_TOKEN`, else `~/.config/imprint-runners/token`, else `gh auth token`. A token in a file keeps working when no keyring is unlocked, for example after a reboot without a login.
2. **Trusted runners.** Redeploy the `imprint-runner` stack in Portainer from [`linux-trusted.stack.yml`](../.github/runners/linux-trusted.stack.yml), with `ACCESS_TOKEN` set as before. The change from the old stack is the host network and the pinned image.
3. **Windows VMs.** `install-host.sh` writes `.github/runners/windows/.env` with a generated password. Then:
   ```bash
   docker compose -f .github/runners/windows/compose.yml up -d
   .github/runners/windows/register-windows.sh
   ```
   - Windows installs unattended, which takes about 20–40 minutes. Follow it on <http://127.0.0.1:8006> and <http://127.0.0.1:8007>.
   - `oem/install.ps1` then installs Chocolatey, Git, PowerShell 7, Inno Setup, CMake, jq and the MSVC Build Tools (another 20–30 minutes), plus the runner. It logs to `windows/shared/<vm>/install.log` (UTF-16).
   - `register-windows.sh` waits for each VM to get that far, mints its registration token (valid for an hour), and waits for the VM to take it. The runner then registers as a service and starts; no restart follows, since it reads the new PATH from `C:\actions-runner\.env`.
   - It then marks the VM installed and deletes its installation ISO (8 GB). Without that marker, dockur treats a VM that was not stopped cleanly as unfinished: after a crash or power loss it wipes the disk and installs Windows again.
   The disks live under `/mnt/internal/vm` (96 GB each, allocated as used). Docker waits for that mount at boot (the drop-in from `install-host.sh`), so the VMs never start on an empty folder on the root disk.

## Setting up the laptop

macOS jobs run in throwaway Tart VMs that a LaunchAgent starts one per job; see
[macOS laptop runners](building.md#macos-laptop-runners) for the prerequisites. Then:

```bash
.github/runners/macos/prepare-image.sh   # bake the golden VM (about 30 GB)
.github/runners/macos/install.sh         # start the supervisor LaunchAgent
```

## Operating

- Slots: `systemctl --user list-units 'imprint-jit-runner@*'`, and their logs with `journalctl --user -u 'imprint-jit-runner@*'`. Add one with `systemctl --user enable --now imprint-jit-runner@pr-x64-3:linux-amd64:imprint-pr`.
- macOS: `tail -f ~/Library/Logs/tech.imprint.vm-runner/supervise.log`, and `tart list` for the VMs. Re-run `prepare-image.sh` after GitHub deprecates the baked runner.
- Registered runners: `gh api repos/alexandruleca/imprint-memory-layer/actions/runners --jq '.runners[] | [.name, .status, .busy] | @tsv'`.
- Windows: `docker compose -f .github/runners/windows/compose.yml stop|start`. Windows 11 runs unactivated, and Windows Update runs inside the VMs.
- A VM whose `install.log` never appears installed Windows without the OEM files. dockur adds them only while installing, and a bind mount whose host folder was deleted and recreated, for example by `git stash -u`, shows the container an empty `/oem`. Instead of reinstalling, copy `oem/install.ps1` into `shared/<vm>/`, open the VM's screen, and in an administrator PowerShell run:
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File \\host.lan\Data\install.ps1 *> \\host.lan\Data\install.log
  ```
- A push to `main` or `dev` runs CI first: Release's `ci` job waits for CI to pass on the same commit before anything is built, so a failing commit is never released and the two do not load the runners at once. If CI fails, fix or rerun it, then rerun Release's failed jobs. A manual Release dispatch skips the wait.
- After a reboot everything starts by itself: the Swarm stack, the slot units through lingering, and the VMs through `restart: unless-stopped`, whose runner services start a few minutes after Windows boots. Jobs that were running are lost; rerun them with `gh run rerun --failed <id>`. A slot's runner name stays taken until GitHub gives up on its lost job, so `imprint-arm64` can take about 10 minutes to come back.
- The Windows runner's PATH is fixed in `C:\actions-runner\.env` at setup. A tool installed into a VM later needs its folder added there, then `Restart-Service actions.runner.*`.
- A VM showing "Installing Windows" after a restart has lost its install: check that `/mnt/internal/vm/imprint-<vm>/windows.boot` exists. The marker can be added by hand to a VM that is installed: `docker exec imprint-<vm> touch /storage/windows.boot`.
- Image updates: bump `2.338.2` in `jit-runners.sh`, `install-host.sh`, `linux-trusted.stack.yml` and `oem/install.ps1` together. The runners are set not to update themselves.

## Known gaps

- Emulated arm64 jobs are several times slower than native ones, so they get 90 minutes instead of 60.
- Every other job and step gets 60 minutes. The runners share one host, and while it is busy, for example installing a Windows VM, jobs take several times longer than on hosted runners; the limits catch hangs, not slow runs.
- The runner images are not GitHub's hosted images. A job that relies on a preinstalled tool needs a setup step or the tool added to the image. Chromium's system libraries, for example, come from `npx playwright install --with-deps`.
- The trusted runners use the host's Docker through its socket, so a path inside the job's workspace does not exist for `docker run -v`. Copy files in and out with `docker cp`, as the Linux voice-engine build does.
- A Windows or macOS regression in a pull request shows up only after merge, or on a push to a branch of this repository.
