---
title: ADR 0111 — Intel Macs are not supported
description: Drop Intel macOS (darwin/amd64) as a platform — no release builds, packages, wheels, voice engines, dependency pins or CI runner — and have install.sh and imprint update refuse an Intel Mac with a clear message; Apple silicon, Linux and Windows are unchanged.
---

- Status: Accepted
- Date: 2026-10-29
- Supersedes: [ADR 0106](0106-intel-mac-laptop-runner.md)
- Amends:
  - [ADR 0012](0012-native-package-runtime-and-silent-installs.md) — the Intel macOS ONNX Runtime 1.23.2 pin is gone; every target installs the current runtime
  - [ADR 0044](0044-native-desktop-voice-platforms.md) — the native speech worker no longer has a macOS Intel target
  - [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md) — the macOS desktop app is built for arm64 only, not as a universal binary
  - [ADR 0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md) — the Intel macOS OpenCV 4.11 pin is gone
  - [ADR 0083](0083-apple-silicon-laptop-runner.md) — no Intel macOS jobs remain, hosted or self-hosted
  - [ADR 0090](0090-macos-installs-llama-cpp-wheels-built-here.md) — only the Apple silicon wheel is built and pinned
- Related: [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) (macOS 13 floor, unchanged), [ADR 0023](0023-update-by-installation-type.md), [ADR 0104](0104-bundle-uv-with-conditional-hash-lock-support.md), [docs/installation.md](../installation.md)

## Context

The owner asked for Intel macOS support to be removed completely: no Intel macOS
release builds or packages and no self-hosted Intel Mac runner, while Apple silicon,
Linux and Windows stay supported.

Intel macOS was the one target that needed its own exceptions throughout the tree:

- ONNX Runtime 1.24+ publishes no Intel macOS wheel, so `requirements/cpu.txt`
  pinned 1.23.2 there through platform markers (ADR 0012), an older runtime line
  whose support for the privacy filter's `QMoE` kernel was never verified
  ([platform validation](../platform-validation.md)).
- Newer OpenCV Intel Mac wheels need macOS 14, so `requirements/base.txt` pinned
  OpenCV 4.11 there (ADR 0076).
- The llama.cpp binding needed its own Intel wheel, built by this repository
  (ADR 0090), and the voice engine its own `macos-x64` build (ADR 0044).
- CI, voice, release packaging and the wheel build ran on a 2017 Intel laptop on
  macOS 13.6 whose `rsvg-convert` had to come from MacPorts (ADR 0106).

## Decision

Intel Macs (darwin/amd64) are not a supported platform.

- Releases ship no `imprint-darwin-amd64.tar.gz` or `.pkg`. `make all`, `make package`
  and `scripts/package-platform.sh` have no darwin/amd64 target, `make installer-macos`
  builds arm64 only, and the macOS desktop app is built for arm64 only.
- Workflows have no Intel macOS jobs: CI, voice engine and native voice checks, the
  release `.pkg` job and the llama.cpp wheel build run on Apple silicon only. The
  `imprint-intel` runner label is retired.
- Requirements and locks carry no darwin x86_64 entries: no ONNX Runtime 1.23.2 or
  OpenCV 4.11 pins, no Intel llama.cpp wheel. An Intel Mac would fall through to the
  generic source-build line of the binding like any other unlisted platform. The
  runtime manifest drops the Intel macOS uv and Qdrant digests, and Qdrant's
  auto-download refuses an Intel Mac.
- `install.sh` on an Intel Mac stops before downloading anything, with "Intel Macs
  are no longer supported". A shell running under Rosetta on Apple silicon reports
  `x86_64`; the script checks `hw.optional.arm64` and installs the Apple silicon
  build there.
- `imprint update` (and `--check`, which the dashboard reads) on a darwin/amd64
  binary reports the same refusal. Under Rosetta it tells the user to install the
  Apple silicon build instead, because an in-place update cannot change the
  installation's architecture.
- The website offers no Intel macOS download and tells a visitor whose browser looks
  like an Intel Mac that it is not supported.

## Rejected alternatives

No alternative evaluation was recorded.

## Consequences

- Existing Intel Mac installations keep working on the version they have but get no
  further releases. Their `imprint update` predates this record: it finds no
  `imprint-darwin-amd64` asset in a new release and stops with "release … has no
  asset named …", without changing the installation.
- Release assets shrink by two files, and the release workflow no longer waits on
  the Intel laptop.
- Older releases keep their Intel assets on GitHub; the website lists them only as
  unclassified files in the release history.
- The Apple silicon llama.cpp wheel release (`llama-cpp-python-v0.3.36`) still holds
  the previously published Intel wheel; nothing pins it any more.
- Dependency locks are simpler: the CPU lock loses ONNX Runtime 1.23.2 and its
  Intel-only dependencies (`coloredlogs`, `humanfriendly`, `mpmath`, `sympy`).
- The self-hosted Intel runner registration and the laptop's runner service are
  outside the repository and must be removed by the owner.

## Invalidation conditions

- If Intel Mac users must be supported again, then a new record restores the target
  together with its dependency pins, a native builder and native validation.
- If the Apple silicon desktop app must also run on Intel through a universal binary
  for another reason (for example a distribution channel that requires one), then
  revisit the arm64-only Electron build without restoring the Intel runtime.

## Validation

On Linux x64: `go vet ./...` and `go test ./...` passed, including a unit test that
`imprint update` refuses darwin/amd64 natively and under Rosetta and accepts every
supported target. The touched Python tests (release validation with arm64-only macOS
bundles, voice targets, download manifest and the Qdrant Intel refusal) and the ADR
record tests passed. The locks were regenerated with uv 0.13.6 using the commands in
[building](../building.md).

Not verified: `install.sh` on a real Intel Mac or under Rosetta, the arm64-only
Electron build and `.pkg` on the Apple silicon runner, a full `build-release` run
without the Intel jobs, and the website build.
