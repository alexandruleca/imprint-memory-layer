---
title: ADR 0112 — CI runs on the owner's machines
description: Every workflow job runs on self-hosted runners on bedrock and the Apple silicon laptop; pull-request code runs only in throwaway containers.
---

- Status: Accepted
- Date: 2026-10-29
- Amends:
  - [ADR 0083](0083-apple-silicon-laptop-runner.md) — pull-request jobs no longer run on hosted machines; Linux PR jobs run in throwaway containers on bedrock, macOS and Windows jobs skip pull requests
- Related: [ADR 0111](0111-intel-macs-are-not-supported.md), [docs/ci-runners.md](../ci-runners.md)

## Context

The owner asked for every workflow to run only on their own machines: bedrock (Ubuntu 24.04, 16 threads, 62 GB, KVM) and the Apple silicon laptop that already runs a runner (ADR 0083), with Windows VMs on bedrock now that SVM is enabled. Before this, Linux, Windows and Linux arm64 jobs ran on GitHub-hosted runners, and pull requests used hosted machines only.

The repository is public and `ci.yml` runs on `pull_request`. A self-hosted runner runs whatever the pull request's workflow files say. Its labels are no boundary: a fork can rewrite `runs-on`. Bedrock's existing trusted runners, the Swarm stack `imprint-runner`, hold a token with admin rights on the repository in their environment and mount the Docker socket, which is root on the host. The fork-PR approval policy was `first_time_contributors` on 2026-10-29.

## Decision

- **Trusted Linux x64** jobs (pushes, releases, manual and scheduled runs) use the `imprint-runner` Swarm stack: `[self-hosted, linux, x64]`, ephemeral containers with the Docker socket. The stack joins the host network so that `127.0.0.1` is the host's, as on hosted runners ([`.github/runners/linux-trusted.stack.yml`](../../.github/runners/linux-trusted.stack.yml)).
- **Pull-request Linux x64** jobs use `imprint-pr`: throwaway containers that a host supervisor ([`jit-runners.sh`](../../.github/runners/jit-runners.sh), systemd user units) starts one per job. Each gets a single-use just-in-time runner config, no Docker socket, no host mounts and no token. These runners carry only their custom label, so no trusted job can land on them.
- **Linux arm64** jobs use `imprint-arm64`: the same throwaway containers under qemu-user emulation on bedrock, for pull requests and trusted runs alike.
- **Windows** jobs use `[self-hosted, windows, x64, imprint-windows]`: two Windows 11 VMs on KVM through dockur/windows, with 4 cores, 8 GB and 96 GB each.
- **macOS** jobs stay on the laptop (`imprint-apple-silicon`).
- Pull requests skip the Windows and macOS jobs. Those VMs and the laptop persist between jobs, so PR code never reaches them.
- On these runners, `runner.os == 'macOS'` replaces the old hosted/self-hosted test for the laptop's persistent toolchain.

## Rejected alternatives

- **Every job local, fork PRs included, with manual approval as the only control.** The owner chose throwaway containers for PRs instead. Approval alone runs reviewed-but-hostile code with the host's Docker group.
- **Native arm64 in a Linux container on the laptop.** It would be faster than emulation, but the owner chose emulation on bedrock, which needs no container runtime on the laptop and no awake laptop.
- **A libvirt VM for Windows.** It needs qemu/libvirt installed with sudo plus a manual ISO and driver setup. dockur/windows installs unattended from a compose file, so the owner chose it.
- **The official `ghcr.io/actions/actions-runner` image for throwaway runners.** It has no git, compilers or zip. Checked on 2026-10-29: `myoung34/github-runner:2.338.2-ubuntu-noble` has them and matches hosted Ubuntu 24.04.

## Consequences

- Hosted minutes drop to zero. Jobs queue while bedrock's runners are busy or the laptop sleeps.
- Emulated arm64 is several times slower than native, so arm64 timeouts may need raising.
- Windows and macOS regressions in a pull request show up only after merge or on a push to a branch of this repository.
- The images lack parts of GitHub's hosted toolchain. Jobs that assumed a preinstalled tool fail until it is added to the image or to a setup step.
- Windows 11 runs unactivated. The VMs need the owner's maintenance: updates, disk space.
- The trusted runners can still be reached by a workflow that names their labels. Any outside PR that edits workflow files must be reviewed before it is approved to run.

## Invalidation conditions

- If GitHub offers runner groups or workflow restrictions to this repository, then trusted runners should be restricted to protected refs and this record revisited.
- If emulated arm64 jobs keep timing out or take more than an hour, then a native arm64 runner (container on the laptop) replaces emulation.
- If bedrock cannot run the Windows VMs alongside its other services, then Windows jobs move elsewhere and this record is amended.
- If an outside contributor's PR must run Windows or macOS checks before merge, then a throwaway Windows or macOS runner is needed.

## Validation

- Executed on bedrock, 2026-10-29:
  - A just-in-time x64 runner minted by `jit-runners.sh` registered, reported online with the single label `imprint-pr`, and was removed.
  - actionlint 1.7.7 accepts every workflow and reports only the five shellcheck notes that existed before.
  - `docker compose config` accepts the Windows compose file.
- Not yet executed:
  - The emulated arm64 runner (qemu-user-static is not installed yet).
  - The Windows VMs and their OEM setup.
  - The host-networked trusted stack.
  - Any workflow run on the new routing.
