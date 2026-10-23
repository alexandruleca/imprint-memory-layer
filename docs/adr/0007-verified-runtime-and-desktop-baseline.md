---
title: ADR 0007 — Verified runtime and desktop baseline
description: Hashed Python profiles, checksum-verified downloads, Electron 45 and the macOS 13 desktop floor.
---

- Status: Accepted
- Date: 2026-10-23
- Extends: ADR 0004; supersedes its deferred hash-lock, Electron and Host-defense items.

## Context

The earlier audit left Electron 35 advisories, unverified runtime archives and unlocked optional profiles. The desktop installer advertised an OS floor incompatible with newer Electron. The container used Python 3.11, an unlocked install, missing runtime paths and a committed dashboard export.

## Decision

Use Electron 45.0.0 with locked builder dependencies and require macOS 13 for the desktop app/pkg, consistent with [Electron's published platform changes](https://www.electronjs.org/docs/latest/breaking-changes#removed-macos-12-support). Record this compatibility change explicitly. macOS 11/12 users are outside this desktop support target; separately built browser/CLI variants need their own validation. Keep managed Python 3.12; host fallback accepts 3.12–3.13, with the primary native CI matrix on 3.12.

Hash-lock base, GPU and LLM requirements; enforce hashes when installing those locks. Compile overlays against the base lock. Existing GPU repair/source-build paths still make separate package requests and are not fully reproducible; full wheel availability on every native profile remains unverified.

Maintain a checked-in SHA256 manifest for pinned uv and Qdrant release archives. Verify before extraction, safely extract tar archives and atomically promote Qdrant binaries. Refuse unmanifested auto-download versions; explicitly managed binaries remain possible. Install/update verifies release SHA256SUMS before extraction. These hashes detect corruption and unexpected bytes; a checksum fetched from the same compromised release is not independent publisher authentication. Model revisions and Python distribution provenance need a stronger signed/pinned policy separately.

Serialize Qdrant first startup with a shared cross-process file lock and recheck health after acquiring it. Keep the lock inode. Honor `IMPRINT_DATA_DIR` in both Go and Python.

Use Python 3.12, a managed venv, hashed requirements, the runtime manifest and a newly built dashboard in the container. Resolve the CLI through `/app/bin` and set PYTHONPATH. Container API may explicitly bind its network namespace (`--container-bind`); publish the host port on loopback. Validate loopback Host as well as Origin, and reject normal CLI public binds. Authenticated remote dashboard mode is not implemented. This is not a filesystem sandbox or protection against local software.

## Alternatives

Retaining old Electron leaves known runtime advisories. Raising the OS floor without documentation conceals a compatibility change. Version pins alone cannot verify downloaded bytes. Requiring offline installers immediately needs native Python/wheel/model redistribution and licensing work beyond this remediation.

## Consequences

Older desktop OS versions lose the declared desktop target. First installation still needs networking. Signed/notarized artifacts require maintainer credentials. Native installer and graphical behavior remain release gates, not inferred from Linux compilation. Container images grow because they include the complete CPU runtime.

## Validation

Electron npm audit and Linux AppImage build; corrupted/missing checksum tests; hashed Linux CPU install; actual Qdrant verified download/start/write/read; concurrent-start regression; container build/runtime checks and native workflow definitions. Record each result separately in platform validation.
