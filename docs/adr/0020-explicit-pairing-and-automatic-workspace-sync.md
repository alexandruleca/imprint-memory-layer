---
title: ADR 0020 — Explicit pairing and automatic workspace sync
description: Opt-in device capabilities, scoped automatic merge and acknowledged peer revocation.
---

- Status: Accepted
- Date: 2026-10-23
- Related: ADR 0016, ADR 0018

## Context

One-time encrypted sessions require a new code for every transfer. Device
fingerprint trust skips an approval prompt but does not authenticate future
sessions or authorize unattended disclosure. Users want previously paired
machines to synchronize when both are online, with pause and forget controls.

## Decision

Keep one-time sync as the default. Both devices explicitly opt into pairing
within an already PIN-authenticated, device-approved encrypted session. Create
an independent 256-bit capability and random opaque relay room, bind it to the
device identifiers, local/remote workspace names, relay address and the actually
approved phase intersection. A declined push cannot become a future automatic
push. Re-pairing replaces the same device/workspace mapping. No global relay
device discovery or fingerprint-only authentication is introduced.

Reuse the existing SPAKE2/AES-GCM transport and data import/export implementation
for automatic sessions. Lexicographically ordered identifiers determine relay
roles. The dashboard backend runs a worker; `imprint sync watch` runs it without
the dashboard. An OS lock gives one worker ownership per data directory, and
SQLite transactions serialize CLI/API controls. Credentials are local, private
data, excluded from sync/snapshot exports and the public peers endpoint.

Each connection exchanges authenticated workspace identity, enable/revocation
state and local content digests before granting data transfer. Pausing either
side prevents both phases. Unchanged endpoint digests skip vector transfer.
Only completed transfers advance acknowledged digests; hash the pre-transfer
snapshot so concurrent writes are not accidentally marked delivered. Imports
may trigger one extra deduplicating pass. This is merge sync, not a distributed
deletion/change-log protocol.

Forgetting requires confirmation naming both devices and all paired workspaces.
Revoke locally immediately, cancel active background sessions, remove legacy
fingerprint trust and queue an encrypted forget notification. A connected peer
acknowledges and removes all capabilities for that device; both remove their
local pair records. Existing memories remain. An offline peer is explicitly
shown as pending, never as already forgotten. A pending tombstone retains only
the capability needed to deliver revocation, and cannot transfer workspace data.

## Alternatives

Silently reusing one-time PINs or trusting fingerprints would widen the original
authorization and enable impersonation. Relay-wide device listings reveal
unnecessary metadata. A persistent relay mailbox could deliver revocations when
devices are online at different times, but requires durable relay storage and
an additional authenticated mailbox protocol; the existing stateless relay is
retained. Polling full exports without digests wastes bandwidth.

## Consequences

Both Imprint workers must run and reach the same relay. Reconnects are best
effort, normally about a minute apart, with bounded sessions and no LLM usage.
The relay learns a stable opaque room identifier and connection metadata.
An offline forget needs both workers to overlap online later. A lost final
acknowledgement can leave the initiator showing pending rather than claiming
confirmed removal; re-pair explicitly if recovery is needed. Previously shared
memories and copies cannot be retracted remotely. Local workspace digests still
scan exported records including vectors; large-corpus incremental checkpoints
remain future work. The current import cancellation boundary is the current
batch, not an all-or-nothing rollback.

## Validation

Real WebSocket relay tests with separate temporary device profiles exercise
mutual consent, authenticated reconnection, unchanged snapshots, receive-only
grants, declined pushes, pause/resume, workspace mismatch, encrypted frames and
acknowledged revocation. API tests require a literal confirmation boolean and
verify credentials are absent. Native filesystem lock tests run in the platform
test matrix; local execution evidence is Linux. Native installers and performance
on macOS/Windows are not established by these Linux sync tests.
