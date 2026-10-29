---
title: ADR 0115 — Apple silicon jobs run in disposable Tart VMs
description: macOS jobs run one per freshly cloned Tart macOS VM on the owner's Apple silicon laptop instead of on the laptop itself, and the macOS package job installs and smokes the package again.
---

- Status: Accepted
- Date: 2026-10-29
- Scope: macOS workflow jobs and the Outland laptop
- Amends:
  - [ADR 0083](0083-apple-silicon-laptop-runner.md) — macOS jobs run in a disposable VM instead of under the owner's account, and the package is installed again
  - [ADR 0112](0112-ci-runs-on-the-owners-machines.md) — the macOS runner no longer persists between jobs
- Related: [docs/ci-runners.md](../ci-runners.md)

## Context

ADR 0083 registered a runner that executes jobs directly on the owner's Apple
silicon laptop, as the owner's user; ADR 0112 kept macOS jobs there. Asked whether
that runner used disposable VMs, the answer was no: Tart was not installed, the job
work folder persisted, and the runner's `.path` exposed the owner's govm, nvm,
Codex and editor shims to every job. Because the laptop is not disposable, the
macOS package job only expanded and validated the `.pkg` payload; its install step
was gated to non-macOS runners and so never ran. The owner asked to run the macOS
runner in throwaway VMs and to restore the full package test.

## Decision

A login LaunchAgent on the laptop ([`.github/runners/macos/supervise.sh`](../../.github/runners/macos/supervise.sh))
polls the repository for queued jobs carrying the `imprint-apple-silicon` label.
For each one it clones a local golden Tart VM, boots it headless, mints a GitHub
just-in-time runner configuration on the host and passes it to the guest over
`tart exec` stdin. The guest runner accepts exactly one job and exits; the
supervisor then deletes the VM and the runner registration. One VM runs at a time.

The golden VM is baked by [`prepare-image.sh`](../../.github/runners/macos/prepare-image.sh)
from Cirrus Labs' `macos-tahoe-base` pinned by digest, adding Homebrew `cmake`,
`librsvg` and `python@3.12` and the latest GitHub Actions runner verified against
the SHA256 in its release notes. Its runner environment sets
`IMPRINT_DISPOSABLE_RUNNER=1`.

The VM runner keeps the labels `[self-hosted, macOS, ARM64, imprint-apple-silicon]`,
so every macOS workflow moves to it unchanged, and the bare-metal
`Outland-apple-silicon` runner is retired. The macOS `package-macos` job installs
the `.pkg` with `sudo installer`, bootstraps it and runs the installed runtime smoke
after the payload validation; the install step refuses to run on a self-hosted
runner without `IMPRINT_DISPOSABLE_RUNNER=1`. Pull requests still skip macOS jobs.

## Rejected alternatives

- **Keep the bare-metal runner.** It is what the owner asked to replace: jobs run
  with the owner's privileges and files, and it cannot install packages.
- **An always-on VM runner.** Simpler than polling, but it holds 8 GB of the
  laptop's 16 GB while idle; the supervisor boots a VM only for queued jobs.
- **SSH into the guest.** The Cirrus images accept the well-known `admin`/`admin`
  password; `tart exec` through the image's guest agent needs no credential and
  keeps the JIT configuration out of process arguments.
- **Homebrew tap install of Tart.** The `cirruslabs/cli` tap formula (last updated
  2026-06-01) fails under current Homebrew (`depends_on :macos` error); Tart 2.41.1
  is installed from its notarized GitHub release after checksum verification.

## Consequences

- Jobs see a clean macOS Tahoe guest each time: no host files, credentials, shims
  or leftovers from earlier jobs. The host keeps the GitHub token.
- `installer`, bootstrap from `/Applications` and the installed runtime smoke run
  again for the macOS package.
- Each job pays a clone and boot (30 seconds to 2 minutes measured) and starts with
  cold local caches: pip and npm downloads repeat every job, and CI's Go modules
  come from setup-go's GitHub cache, which macOS jobs now use. Builds run in at
  most the VM's 6 CPUs and 8 GB, less than the bare-metal runner had.
- Builds use the guest's macOS and Command Line Tools, not the host's; the guest is
  updated only by re-baking from a newer pinned image.
- The supervisor needs a token that can create runner registrations: an optional
  fine-grained token in the keychain item `imprint-vm-runner`, else the owner's
  `gh` login. Polling costs a few API calls every 30 seconds.
- The guest is on Tart's default NAT network and can reach the host and LAN.
  Tart's `softnet` isolation needs a one-time privileged setup and is not enabled,
  so the VM is not yet a boundary for untrusted code and pull requests keep
  skipping macOS jobs.
- Jobs still queue while the laptop sleeps or is offline; the supervisor holds an
  idle-sleep assertion only while a job runs. Tart's fair-source license is free
  for this personal use; Apple's license allows two macOS VMs per Mac, and the
  supervisor runs one.
- A runner version GitHub deprecates stops taking jobs until `prepare-image.sh` is
  re-run.

## Invalidation conditions

- If pull requests need macOS checks before merge, then enable softnet isolation
  and route `imprint-pr`-style macOS jobs to the VM, amending ADR 0112's PR rule.
- If clone-and-boot or cold caches make macOS jobs too slow to be worth it, then
  add persistent caches outside the VM or return those jobs to a hosted runner.
- If GitHub ships native ephemeral VM support for self-hosted macOS, or Tart's
  license stops covering this use, then reconsider the supervisor.
- If the laptop is retired, then re-bake the golden VM on another Apple silicon Mac
  with `install.sh`, or return the jobs to hosted runners.

## Validation

- Executed on Outland (Apple M3, 16 GB, macOS 26.6.2), 2026-10-29:
  - Tart 2.41.1 from its GitHub release: checksum matched, `spctl` accepts it as
    notarized Developer ID (Cirrus Labs, 9M2P8L4D89).
  - `prepare-image.sh` baked `imprint-runner-golden` from the pinned
    `macos-tahoe-base` (26.4 GB compressed): Command Line Tools, Python 3.12.16,
    CMake 4.4.5, rsvg-convert 2.63.4, runner 2.338.2 whose archive matched the
    release-note SHA256; the guest root grew to 93 GB.
  - The supervisor, under `/bin/bash` 3.2 as the LaunchAgent runs it, took three
    queued jobs in three successive VMs, each deleted with its registration
    afterwards: `build-release / voice-engines / engine (macos-arm64)` and
    `build-release / electron-macos` of a dev release run (40883284146), and CI
    `native (macos-latest)` (run 40884007374, from `main`'s workflow). All three
    succeeded. Boot to "Listening for Jobs" took 27 seconds to 2 minutes.
  - The package job's install commands, run in a fresh clone against the published
    `v0.34.0-dev.1` `imprint-darwin-arm64.pkg` (checked against its SHA256SUMS):
    `installer` into an empty `/Applications`, `imprint bootstrap --profile cpu`,
    `scripts/smoke-runtime.py --with-embeddings` (MCP handshake with 19 tools, API
    and dashboard, Qdrant, ONNX retrieval) and the setup-helper check all passed.
  - actionlint reports no new findings; shellcheck is clean on the runner scripts.
- Not yet executed:
  - `build-release`'s `package-macos` job with the new install step: it runs only
    from `main` or `dev`, so its first run is the next release build.
  - `llama-wheels` and `voice-native` in the VM.
  - Softnet network isolation.
