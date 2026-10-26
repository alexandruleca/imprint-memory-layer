---
title: ADR 0073 — Remove a pairing from one device without the other's acknowledgement
description: A paired device can be removed from this device alone, at once, without contacting the relay or the peer; forgetting on both devices stays the normal path.
---

- Status: Accepted
- Date: 2026-10-26
- Amends: [ADR 0020](0020-explicit-pairing-and-automatic-workspace-sync.md) — a pairing can also be removed from this device alone, at once, without the encrypted forget acknowledgement
- Related: [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) (the encrypted session a paired attempt opens), [sync](../sync.md#automatic-sync-between-paired-devices)

## Context

Under ADR 0020, forgetting a paired device revokes it here at once (every pair record for that device becomes `forget_pending`, disabled, untrusted, and its sessions are cancelled), but the records and their credentials are deleted only inside an authenticated exchange, after the other device acknowledges (`sync_pairs.exchange` calls `remove_peer` on `{'forgotten': pair_id}`). A device that never comes back, because it was uninstalled, reset or lost, never acknowledges. Its pairing then shows **Removal pending** forever, the dashboard hides every control on a pending pairing, and the worker keeps opening the paired relay room about once a minute with a capability that can no longer transfer anything.

The owner asked on 2026-10-26: "a user should be able to completely remove a pair for auto sync instead of waiting for the relay to confirm the other device was notified; sometimes devices disappear from uninstalls etc."

## Decision

**Remove from this device.** A confirmed removal deletes this device's pairing with a peer at once, without contacting the relay or the peer. It goes through `remove_peer`, the path an acknowledged forget already uses, so the two cannot drift apart: every pair record for that device fingerprint (every paired workspace) with its relay room, pair secret and checkpointed digests (the store runs with `secure_delete`, so the rows are overwritten), the device's entry in `trusted_devices.json`, and any session running for it in this process. A worker in another process stops the session on its next management tick, about two seconds later, because the record is gone. It works in any state: active, paused or with a forget pending.

- API: `POST /api/sync/peers/{pair_id}` with `{"action": "remove", "confirm": true}`, the same endpoint, validation and local-origin boundary as pause and forget. Confirmation must be the literal `true`.
- CLI: `imprint sync remove <pair-id>`, a new subcommand next to `forget`, with its own `[y/N]` prompt. `forget` keeps its meaning.
- Dashboard: **Remove from this device…** on every pairing, with its own confirmation, which says the other device is not notified and what it will see. It is highlighted, with a hint, when a forget is pending or the device has not answered for 7 days (`last_seen`, or the pairing time when it never reconnected).

**Forget on both devices** is unchanged and stays the normal path while both devices are in use.

**What the other device sees.** Nothing tells it. If it comes back it still holds its pairing and keeps opening the paired room, where this device no longer appears. The protocol cannot tell that apart from this device being offline, so its pairing records the one message that fits both: *The other device hasn't answered since 2026-10-19 14:03; it may be offline or have removed this pairing*, with the time of its last contact. In the consumer role (the higher device id) the attempt gives up on the unanswered key exchange after 30 seconds; in the provider role it waits for the other device to join until the worker's 10-minute attempt limit. Its dashboard row and `imprint sync peers` show the message, and after 7 days without contact its dashboard also highlights removal there.

The provider's attempt used to end at that limit without any error: the sessions caught every task cancellation and reported it as the user's own cancel. They now report only a cancel the user asked for in that session; a cancelled task (the attempt limit, pause, forget, remove, shutdown) reaches whoever cancelled it. The worker turns its own limit into the message above when the other device never answered during the attempt (the authenticated exchange records `last_seen`), and into *The sync did not finish within 10 minutes* when it did; a pause, forget, remove or shutdown still ends the attempt without recording anything. Errors a session reports are recorded as worded, without the `RuntimeError:` prefix.

## Rejected alternatives

- **Expire a pending forget after a while.** It still makes the owner of an uninstalled device wait, and it would silently drop the pairing of a device that is only offline for long (a laptop in a drawer), which the owner did not ask for.
- **Tell the other device anyway.** There is nobody to tell: the relay only forwards between two connected peers, and ADR 0020 rejected a relay mailbox for offline revocations.
- **Let the other device learn that the pairing is gone.** This device would have to keep answering in the paired room, which needs the room and secret that removal deletes. Keeping them is what a pending forget already does.
- **A scope flag on forget** (`{"action": "forget", "scope": "local"}`, `imprint sync forget --local`). In the code and in ADR 0020, forget means revoke, notify, and delete on acknowledgement; local removal is a different operation with a different outcome for the other device. A separate verb keeps each confirmation about one thing and matches the existing `remove_peer`.

## Consequences

- A pairing with a device that is gone can be cleared in one confirmed step on each surface, and nothing about that device is left in the pairing store or the trust list.
- Removal is one-sided by design. Removing a pairing whose device is still in use leaves that device with a dangling pairing that keeps trying the relay until its owner removes it there; the dialog and the CLI prompt say so. Its attempts cost the relay one waiting connection per attempt, as an offline peer already does.
- Memories either device already received stay. Syncing these two devices again needs a new pairing with a fresh code.
- The 7-day highlight is only a hint: a device that is offline for longer is highlighted too, and nothing is removed without confirmation.

## Invalidation conditions

- If the relay gains durable storage, a removed device could leave a revocation for its peer, and one-sided removal could become a delayed two-sided one.
- If the pairing protocol gains a way to answer for a pairing a device no longer holds (for example a per-device rendezvous), the other device could be told the pairing is gone, and its error should say so and suggest removing it.
- If automatic sync stops reconnecting about once a minute, `last_seen` no longer means what the 7-day highlight assumes: change the threshold with the interval.

## Validation

Executed on Linux (WSL2):

- `tests/test_sync_pairs.py`, over a real WebSocket relay with two isolated device profiles: removal from an active pairing and from a pending forget deletes every record, the legacy trust and the stored bytes of the secret, room and id, while the other device keeps its active pairing; removal needs the literal `True`; it covers every workspace with that device and no other device; a running session is cancelled at once in this process and on the next tick when another process removes it, and nothing restarts; the worker on the device left behind records *The other device hasn't answered since …* in both roles (the 30-second wait and the attempt limit shortened), counted from the pairing when it never answered, and `imprint sync peers` lists it; an attempt past its limit after the other device answered records that it did not finish; a pause and a shutdown end an attempt waiting in the room without recording anything; the API refuses a missing or non-literal confirmation, a foreign origin and an unknown pairing; the CLI asks first, does nothing on *n* and reports an unknown pairing. The existing forget, pause and acknowledgement tests still pass.
- `cmd/sync_test.go`: `imprint sync remove` reaches the Python CLI as a subcommand, not as a share code to join.
- `imprint/ui/tests/sync.spec.ts`: the remove dialog, its cancellation and the request it sends; the highlight on a pending forget, on a device last seen 10 days ago and on one never seen since pairing 60 days ago, and none on a device seen recently; the existing forget flow.
- `tests/test_sync_live.py`: a cancel the user asks for still ends a session with `cancelled`, and cancelling the task that runs a session raises instead.

Not validated: removal against a running dashboard with a real peer on another machine, and on macOS or Windows.
