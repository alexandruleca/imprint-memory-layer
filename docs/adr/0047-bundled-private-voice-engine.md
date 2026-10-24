---
title: ADR 0047 — Bundle the private voice engine
description: Native release engines remove the end-user compiler requirement while preserving explicit model downloads and verification.
---

- Status: Accepted
- Date: 2026-10-24
- Related: [0044](0044-native-desktop-voice-platforms.md), [0046](0046-visible-echo-runtime-setup.md)
- Supersedes: ADR 0046's release-time prerequisite/build policy; its download, consent and cancellation policy remains in force.

## Context

Requiring users to install CMake and a C++ compiler makes first-time Echo setup
fragile. Bundling CMake alone would still require a platform compiler and SDK.
The private patched engine is small enough to ship with the desktop release.

## Decision

Build native CPU engines for Linux x64, Windows x64, macOS x64 and macOS ARM64
in a reusable CI workflow. Linux builds use manylinux's glibc 2.28 baseline and
static C++/GCC runtime libraries. Their GPLv3 and GCC Runtime Library Exception
3.1 notices accompany Linux bundles and installed voice caches. Mac retains the macOS 13 baseline; Windows uses
the static MSVC runtime. No compiler, CMake, build tree or developer environment
is distributed. Linux ARM remains outside the voice pilot.

Each supported release archive must contain its matching `imprint/voice-engine`
payload. Packaging verifies native architecture, source/patch/build/binary
fingerprints, executability and notices. The pinned full source archive travels
with the executable for the existing patch verifier and third-party licenses.
Model weights remain optional, outside release archives and installed only after
an explicit user action. Release CI requires the engine artifacts before packaging.

First-time setup verifies the included engine before downloading the models,
checks 2 GB free space and atomically copies the engine into the private voice
cache, installing its metadata last. Launch retains the full integrity check.
Cached builds from earlier build versions are offered setup again, without
redownloading already verified weights. Damaged bundles produce an update/reinstall
action, rather than silently downloading/building an unverified replacement.

Source checkouts without an engine bundle retain the developer CMake/compiler
path and 4 GB space check. The source builder can build/export an engine without
downloading the speech weights. No user configuration or microphone state changes.

## Alternatives

- Ship CMake: still needs a compiler/SDK and increases setup complexity.
- Install a system toolchain automatically: large download and machine-wide changes.
- Download an engine from a separate endpoint at first use: adds another artifact
  trust/update mechanism; including it in the existing release is simpler.

## Consequences and validation

Release artifacts grow by the engine and pinned source archive. Every supported
engine must be built and executed on its native OS. Native voice CI exercises
real speech and managed setup into a fresh disposable cache with PATH empty,
seeding only previously verified model weights. This establishes compiler-free
installation without repeatedly downloading a gigabyte in the GUI smoke.
Unit fixtures verify export excludes weights, missing tools do not block bundled
setup, and target/tamper/old-build checks remain mandatory. Native execution and
remaining installer/manual gates are recorded in platform validation; a workflow
definition alone does not establish support.
