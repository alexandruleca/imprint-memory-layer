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
| `self-hosted, macOS, ARM64, imprint-apple-silicon` | trusted only | the Apple silicon laptop ([ADR 0083](adr/0083-apple-silicon-laptop-runner.md)) | macOS jobs |

Pull requests run no Windows or macOS jobs. Those machines persist between jobs, so PR code never reaches them.

## Security

The repository is public. A pull request's workflow files decide what its jobs run, and a fork can name any label, including the trusted ones. Labels are no boundary:

- The throwaway runners (`imprint-pr`, `imprint-arm64`) are the only ones built for untrusted code. Each container takes one job with a single-use just-in-time config. It gets no token, no Docker socket and no host mounts, and it is deleted afterwards.
- The trusted Swarm runners hold an admin token in their environment and mount the Docker socket, which is root on bedrock. The Windows VMs and the laptop keep state between jobs.
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
   ```
   - Windows installs unattended, which takes about 20–40 minutes. Follow it on <http://127.0.0.1:8006> and <http://127.0.0.1:8007>.
   - `oem/install.ps1` then installs Chocolatey, Git, PowerShell 7, Inno Setup, CMake, jq and the MSVC Build Tools, plus the runner. It logs to `windows/shared/<vm>/install.log`.
   - When the log says it is waiting for `runner.token`, run:
     ```bash
     .github/runners/windows/register-windows.sh
     ```
   The disks live under `/mnt/internal/vm` (96 GB each, allocated as used).

## Operating

- Slots: `systemctl --user list-units 'imprint-jit-runner@*'`, and their logs with `journalctl --user -u 'imprint-jit-runner@*'`. Add one with `systemctl --user enable --now imprint-jit-runner@pr-x64-3:linux-amd64:imprint-pr`.
- Registered runners: `gh api repos/alexandruleca/imprint-memory-layer/actions/runners --jq '.runners[] | [.name, .status, .busy] | @tsv'`.
- Windows: `docker compose -f .github/runners/windows/compose.yml stop|start`. Windows 11 runs unactivated, and Windows Update runs inside the VMs.
- A VM whose `install.log` never appears installed Windows without the OEM files. dockur adds them only while installing, and a bind mount whose host folder was deleted and recreated, for example by `git stash -u`, shows the container an empty `/oem`. Instead of reinstalling, copy `oem/install.ps1` into `shared/<vm>/`, open the VM's screen, and in an administrator PowerShell run:
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File \\host.lan\Data\install.ps1 *> \\host.lan\Data\install.log
  ```
- Image updates: bump `2.338.2` in `jit-runners.sh`, `install-host.sh`, `linux-trusted.stack.yml` and `oem/install.ps1` together. The runners are set not to update themselves.

## Known gaps

- Emulated arm64 jobs are several times slower than native ones, so they get 90 minutes instead of 20.
- The runner images are not GitHub's hosted images. A job that relies on a preinstalled tool needs a setup step or the tool added to the image. Chromium's system libraries, for example, come from `npx playwright install --with-deps`.
- The trusted runners use the host's Docker through its socket, so a path inside the job's workspace does not exist for `docker run -v`. Copy files in and out with `docker cp`, as the Linux voice-engine build does.
- A Windows or macOS regression in a pull request shows up only after merge, or on a push to a branch of this repository.
