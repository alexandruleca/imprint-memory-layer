---
title: ADR 0002 — Validate releases before publication
description: Shared native build pipeline, complete asset publication and architecture-aware staging.
---

- Status: Amended
- Date: 2026-10-23
- Amended by: [ADR 0014](0014-main-only-release-packaging.md) — automatic dev releases are removed, and every release build, manual dispatch included, requires main
- Related: [platform validation](../platform-validation.md)
- Restructured: 2026-10-25

## Context

Stable and dev workflows duplicated packaging. Releases became visible before `.pkg`/`.exe` jobs finished; their checksums omitted native installers. Linux ARM archives received an x64 Electron executable. Packaging deleted staged trees needed by documented installer targets, and UI source was not rebuilt during release.

## Decision

Use one reusable build workflow for stable/dev channels. Rebuild the dashboard; build separate Linux Electron architectures and native macOS/Windows shells; stage and validate all five platform archives; build and smoke-test native installers and Linux runtimes; then create the release with the full asset set and checksums. Native macOS installer jobs use matching Intel/ARM runners. Keep a manual build-only dispatch for diagnosing releases without publishing.

Use `scripts/package-platform.sh` for both packaging targets. Keep staged trees, exclude local state/dependencies and inspect native executable architectures. A missing Electron artifact fails release packaging; local browser-only packaging is allowed and explicitly reported.

## Rejected alternatives

Cross-compiling the Go binary alone misses runtime, frontend and installer errors. Publishing archives first creates partial public releases. Copying one desktop executable into multiple architectures does not provide compatibility. Separate stable/dev implementations would continue drifting.

## Consequences

Release jobs take longer and use multiple hosted runners. Failed native checks block publication. The final GitHub upload is not a database transaction, but native build failures no longer expose a purportedly complete release. No release/tag is published by local validation. macOS/Windows signing and actual GUI first-run/tray testing still require additional work; successful smoke tests do not establish every OS/version combination.

## Invalidation conditions

- If GitHub releases gain an atomic publication that exposes every asset at once or none, the ordering that keeps partial releases private can rely on it; native validation before publication stays.
- If hosted native runners stop being available for a supported target (Intel or Apple Silicon macOS, Windows, Linux ARM), that target can no longer pass this gate: change its support explicitly rather than skip its checks.

## Validation

Local cross-compilation and Linux packaging/runtime tests are recorded separately. Workflow syntax is checked with actionlint. Native workflows must execute after the change is pushed; their results cannot be inferred from Linux tests.
