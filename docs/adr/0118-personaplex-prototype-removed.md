---
title: ADR 0118 — The PersonaPlex prototype is removed; the voice memory snapshot stays
description: Remove the unused PersonaPlex runtime adapter, its launcher and its test-only dependencies, and keep the read-only memory snapshot the live voice uses in a module of its own.
---

- Status: Accepted
- Date: 2026-10-30
- Supersedes: [ADR 0041](0041-personaplex-startup-memory-prototype.md)
- Related: [ADR 0042](0042-lfm-audio-cpu-experiment.md) (the LFM audio experiment chosen instead), [ADR 0043](0043-live-voice-and-echo-companion.md) (the live voice that keeps the snapshot)

## Context

ADR 0041 added an experimental adapter for the PersonaPlex speech model: a launcher for a separately provisioned, pinned PersonaPlex checkout (`imprint/personaplex.py serve`, `imprint/_personaplex_runtime.py`) and a read-only MCP memory snapshot injected at startup. The owner then chose the smaller LFM audio model (ADR 0042), and the live voice and Echo (ADR 0043) were built on it. On 2026-10-30 the owner said PersonaPlex is not used.

The adapter still cost something: CI installed a test-only dependency set for it (`requirements/personaplex-test.lock.txt`, aiohttp among it), release validation guarded against shipping that set, and its four runtime-adapter tests errored on any machine without those dependencies. Meanwhile the live voice imported its snapshot code and error type from `imprint/personaplex.py`, so the live feature depended on a module named after the removed one.

## Decision

- **Removed:** the PersonaPlex launcher and runtime adapter, its pinned source and model revisions and source checks, its test-only requirements and CI's install step for them, the release check for those files, the runtime-adapter tests and the prototype guide.
- **Kept, moved:** the read-only memory snapshot (prepare through stdio MCP, bounded prompt, private export, validated load) moves unchanged to `imprint/voice_memory.py`, with its tests in `tests/test_voice_memory.py`. Its error type is `VoiceMemoryError`. A snapshot no longer carries PersonaPlex's revisions: the caller stamps the identity of the voice runtime it is for, and loading requires that identity, as the live voice already did.

## Rejected alternatives

- **Deleting only the failing tests.** The adapter would have stayed in the product untested, with its dependencies still installed in CI.
- **Keeping `imprint/personaplex.py` as the snapshot's home.** The live voice would keep importing from a module named after a removed feature.

## Consequences

- CI installs one dependency set fewer, and the test suite no longer depends on aiohttp.
- Snapshots exported by the PersonaPlex prototype cannot be loaded by anything; the live voice never read them.
- The feasibility study and platform validation records keep describing PersonaPlex as history.

## Invalidation conditions

- If a full-duplex speech model like PersonaPlex is wanted again, then a new record designs its integration; the snapshot module can supply its startup memory.

## Validation

- The voice tests pass on Linux: `test_voice_memory` (the snapshot's export, validation and real stdio MCP bridge), `test_lfm_audio` (including that a snapshot made for another voice runtime is refused), `test_voice`, `test_voice_setup`, `test_voice_bundle` and `test_release_validation`.
- No code, script or workflow refers to PersonaPlex any more; the documents that do are records of what was tried.
