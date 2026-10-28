---
title: ADR 0106 — Intel Mac laptop runner
description: Route trusted Intel macOS jobs to the owner's Intel laptop, as ADR 0083 did for Apple silicon, and validate the Intel package payload there instead of installing it.
---

- Status: Accepted
- Date: 2026-10-28
- Scope: Repository Intel macOS workflows and the Narnia laptop
- Amends:
  - [ADR 0083](0083-apple-silicon-laptop-runner.md) — Intel jobs no longer stay hosted, and the hosted Intel install check is gone
  - [ADR 0090](0090-macos-installs-llama-cpp-wheels-built-here.md) — the Intel wheel is built on the laptop runner instead of GitHub's hosted Intel runner
- Related: [docs/building.md](../building.md#macos-laptop-runners)

## Context

The owner asked for a runner on their Intel laptop that takes the Intel macOS jobs,
like the Apple silicon runner of ADR 0083. CI, bundled voice engines, native voice
checks, release packaging and the llama.cpp wheel build still used hosted
`macos-15-intel`. The laptop is a 2017 MacBook Pro (Core i7-7920HQ) on macOS 13.6.

The hosted Intel package job was the only one that installed the `.pkg`: it runs
`sudo installer` into `/Applications`, bootstraps and smokes the runtime. A runner
under the owner's login account cannot run `sudo` unattended, and installing
release builds on a personal laptop would overwrite a real application.

## Decision

Register one repository runner, `Narnia-intel`, outside the checkout with the custom
label `imprint-intel`, and run it as a login LaunchAgent. Route trusted push and
manual Intel macOS jobs to `[self-hosted, macOS, X64, imprint-intel]`; PR jobs keep
hosted `macos-15-intel`. The Intel package job validates the expanded payload on
the laptop as the ARM64 job does, so no CI job installs a macOS package any more.
The owner chose this over keeping the release-time Intel job hosted.

## Rejected alternatives

- **Keep only the Intel package job hosted.** It would have kept one full native
  install test at small cost, since it runs only on releases. The owner chose to
  move every Intel job off hosted runners.
- **Install the package on the laptop.** It needs unattended `sudo` and would
  replace the owner's application and user state, as ADR 0083 found for ARM64.

## Consequences

- Hosted macOS charges end for trusted runs; PRs still use hosted macOS.
- Installing the `.pkg`, bootstrap from `/Applications` and the installed runtime
  smoke are no longer tested on any Mac. Install regressions surface only on manual
  testing until a disposable Mac exists.
- The laptop runs one job at a time; Intel jobs queue while it sleeps or is offline,
  with no hosted fallback.
- Builds now run on macOS 13 with its Command Line Tools instead of macOS 15 and
  Xcode. The llama.cpp wheel and voice engine already target macOS 13, so their
  deployment floor is unchanged; a newer SDK is no longer exercised.
- The runner has the owner's local privileges. As in ADR 0083, PR routing is not an
  isolation boundary and untrusted workflow runs must not be approved.

## Invalidation conditions

- If a macOS install regression ships that the payload check could not catch, then
  restore a full install test on a disposable Mac or a hosted Intel runner.
- If the laptop cannot keep up or is retired, then move the `imprint-intel` label to
  another Intel Mac or return the jobs to hosted runners.
- If the toolchain on macOS 13 can no longer build a job (for example a newer Xcode
  or Electron requirement), then upgrade the host or return that job to hosted.
- If untrusted contributors need self-hosted execution, then use isolated disposable
  runners.

## Validation

Registered the checksum-verified official runner 2.338.2 (`osx-x64`) on macOS 13.6.6
and started its LaunchAgent; GitHub lists it online with the `imprint-intel` label.
Job execution evidence is recorded in the change report. Not verified: a full
`build-release` or `llama-wheels` run on this runner.
