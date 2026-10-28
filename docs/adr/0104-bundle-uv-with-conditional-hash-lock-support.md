---
title: ADR 0104 — Bundle uv with conditional hash-lock support
description: Pin uv 0.13.6 and verified archive hashes so fresh native bootstrap resolves the platform-specific ONNX pins used by audio ingestion.
---

- Status: Accepted
- Date: 2026-10-28
- Related: [ADR 0007](0007-verified-runtime-and-desktop-baseline.md), [ADR 0096](0096-cpu-whisper-runtime-for-audio-imports.md)

## Context

The Linux x64 and ARM64 prerelease smoke jobs failed fresh bootstrap with the bundled uv 0.5.11: `--require-hashes` rejected an apparently unpinned `onnxruntime` requirement. The CPU lock actually contains exact, hashed ONNX versions under platform markers; faster-whisper adds ONNX as a transitive requirement. Installing with pip in branch CI succeeded, and an already-populated local venv masked the failure. A fresh disposable venv reproduces it with uv 0.5.11. The identical locks resolve with uv 0.13.6.

## Decision

Bundle pinned uv 0.13.6 on every existing native target. Update the Makefile, shell and PowerShell fetcher defaults together. Record its archive SHA256 values from the official Astral GitHub release in the runtime manifest and keep verification before extraction/execution. Preserve `--require-hashes`, the existing package/version pins and platform markers, including Intel macOS's older ONNX runtime and Apple Silicon's macOS-13-compatible PyAV version.

Do not regenerate tracked CLI binaries or weaken installer dependency checks. Test fresh disposable bootstrap, not only installation into an existing environment. The existing release smoke jobs remain the native installer validation gate. A development checkout using a managed uv from PATH also needs a compatible version; the old bundled 0.5.11 is not suitable for these fresh audio-enabled installs.

## Rejected alternatives

- **Remove hash enforcement.** That would discard the verified-runtime boundary to work around a resolver issue.
- **Use one unconditional ONNX pin.** A Linux-only reproduction with an unconditional pin succeeds on the old uv, but shipping that change would lose the Intel macOS exception.
- **Rely on populated venv checks.** The same old uv succeeds when the dependencies are already installed, which does not validate first installation.

## Consequences

First installations use a newer verified installer binary and can resolve the conditional hashed requirements. Managed Python remains on the 3.12 line; uv may select a newer patch release. Existing CLI arguments and dry-run replacement detection remain: a disposable replacement probe still reports `Would uninstall`, which is what frees the runtime before replacing loaded packages. Archive provenance remains checksums from the official release rather than independent publisher signatures, as in ADR 0007.

## Invalidation conditions

If a future uv changes planning output or supported native platforms, validate runtime-unload detection and every packaged target before changing the pin. If fresh bootstrap regresses, reproduce against empty venvs and retain hash enforcement while fixing the resolver or locked requirements.

## Validation

On Linux/WSL, fresh-venv dry runs fail with uv 0.5.11 and resolve the same base/CPU locks with uv 0.13.6. The downloaded Linux archive matches its official release digest. A disposable package-replacement dry run retains the `Would uninstall` signal. Download tests check that the version defaults and supported-target checksums stay aligned. Fresh full CPU bootstrap, repeat bootstrap and the native audio/MCP/dashboard/Qdrant runtime smoke passed in a disposable stage with Python 3.12.16. Repeat bootstrap disabled shell alias writing; the first run's scratch alias was removed without changing unrelated shell configuration. Native prerelease jobs remain required before promotion to main.
