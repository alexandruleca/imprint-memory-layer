---
title: ADR 0018 — Single sync implementation and end-to-end encryption
description: Live sync runs in one Python implementation for CLI and dashboard, every session is end-to-end encrypted with SPAKE2 and AES-256-GCM, sessions start from one share code, and joining receives unless the user confirms sending.
---

- Status: Accepted
- Date: 2026-10-23
- Amends: [ADR 0016](0016-sync-direction-and-workspace-sharing.md) — replaces its two implementations and removes its compatibility rules for older clients; its direction policies and workspace selection stand
- Related: [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md) (embedding identity), [ADR 0017](0017-device-provenance-and-local-project-paths.md) (device provenance)
- Restructured: 2026-10-25

## Context

The sync protocol was implemented twice: in Go for the CLI (`cmd/sync.go`, with export/import as Python scripts embedded in Go strings) and in Python for the dashboard (`imprint/sync_ws.py`). Every protocol change had to be made, tested and kept in step twice; ADR 0016's direction policy, and the device provenance of ADR 0017, each touched both. The two implementations also drifted: only the Go CLI could use a self-hosted relay or resume an interrupted download, and only Go answered `/sync/status`, an endpoint no client requested.

The protocol was not end-to-end encrypted. The joining side's HELLO carried the session PIN in clear, and every memory, vector and fact crossed the relay as plaintext JSON. TLS terminated at the relay, so whoever operated a relay — including the default public relay — could read everything synced through it, and, having seen the PIN, impersonate either side.

## Decision

**One implementation.** `imprint/sync_ws.py` is the only implementation of the protocol: async-generator sessions yielding events. The CLI is a terminal front-end for them (`imprint/cli_sync.py` `serve` / `join`), and `imprint sync` in Go only starts it, forwarding stdin for the approval prompt and propagating the exit code. The dashboard streams the same events over SSE. Features one side had now serve both: custom relays (the dashboard's Serve tab gains a relay field and Receive accepts any target form), and resumable downloads for the CLI. The relay stays in Go. `/sync/status`, the per-room legacy buffer migration (a layout no release shipped) and the Go fingerprint writer are removed; device identity comes from `imprint/device.py` alone.

**End-to-end encryption (protocol 2).** In `imprint/sync_crypto.py`:

1. The consumer and provider exchange SPAKE2 messages (`spake2` 0.9, Ed25519, asymmetric roles), keyed by the PIN, with the protocol label and room ID in both identities. This is the only cleartext.
2. HKDF-SHA256 derives one key per direction. Every later frame is a binary WebSocket message sealed with AES-256-GCM, nonce = that direction's frame counter.
3. The consumer's first sealed frame is HELLO (device identity, no PIN). A provider that cannot open it reports a wrong PIN and ends the session, so a relay gets one online PIN guess per session and nothing to test guesses against offline. Approval, the grant and all data follow inside the channel.
4. Any frame that fails to open, or a text frame after the exchange, ends the session; nothing from an unfinished stream is stored. Before the channel exists a peer may send an unauthenticated text notice (`{"status", "body", "version": 2}`) that can only end a session.

**One share code.** The serving side prints `<room>-<pin>` (behind `wss://<host>/` on a relay other than the default) and `imprint sync <code>` takes it as one argument; `--pin` is gone. Releases before protocol 2 need `--pin`, so given a code they stop before connecting instead of sending its PIN in clear. The dashboard and `/api/sync/receive` take the same `code`.

**Joining receives by default.** A code is all a machine needs to join, so whoever hands one out must not receive the joiner's workspace by default. Joining is receive-only unless `--two-way` or `--send-only` (dashboard/API `direction`) is given. The provider's sealed `200` carries its device identity next to the grant; before any push, the consumer shows that device and asks (`confirm_push`, answered through the same decision channel as the provider's approval). Declining skips the push and keeps the pull. Serving keeps its two-way default: it only imports what a joiner chooses to send.

**No fallback.** Protocol 1 peers are rejected — an older consumer gets status 426 telling it to update; a newer consumer recognises an older provider's reply. A plaintext fallback would let a relay force it. With every peer on protocol 2, ADR 0016's compatibility rules for older clients are removed: a grant must be an object, and a request outside it is answered with 403 and ends the session instead of being drained.

## Rejected alternatives

- **Encrypt in both implementations.** Rejected: interop between two hand-written crypto paths is where security bugs live, and the duplication cost would recur on every later change.
- **Move the protocol to Go.** Rejected: export/import, storage, provenance and the dashboard are Python; Go already shelled out to Python for all data work.
- **Key from the PIN directly (PBKDF/Argon2 of the PIN).** Rejected: a relay recording traffic could brute-force an 8-character PIN offline, and could still act as a man in the middle at the handshake. A PAKE gives one online guess per session.
- **CPace or OPAQUE.** CPace needs hash-to-curve and has no mature Python implementation; OPAQUE is for long-term client/server passwords. SPAKE2 matches this exact setting (short code, untrusted rendezvous relay) and the chosen package is the one magic-wormhole pins.
- **Keep the relay trusted and rely on self-hosting.** Rejected as the default: the public relay is the default path.

## Consequences

- The relay sees room IDs, the two exchange messages, ciphertext sizes and timing, and IP addresses — not the PIN, devices or data. A relay can still deny service.
- Both machines must run a release with protocol 2; mixed versions fail with an explicit message, and an older joining client refuses the share code before connecting.
- Scripts that relied on joining to push must add `--two-way` (or `--send-only`) and answer the confirmation; joining no longer sends anything unasked.
- `spake2==0.9` (pure Python, MIT, depends only on `cryptography`, which was already locked) is added to `requirements/base.txt` and `base.lock.txt` with hashes. Go needs no crypto dependency.
- ~1,800 lines of Go protocol code and its tests are removed. `cmd/device_test.go` is removed with the Go fingerprint writer: the race it guarded (Go and Python creating different identities) cannot occur, and Python's concurrent initialisation keeps its own test.
- Device trust remains a consent gate keyed by a non-secret fingerprint; the PIN is the authentication. Trusted fingerprints auto-accept and fingerprints are public (they travel in record provenance), so the PIN must keep its length: each session allows one guess, and anything that can start sessions repeatedly could otherwise grind a short PIN.
- The resume buffer records its destination workspace, fixing a regression risk from ADR 0016 where a resumed download could land in whichever workspace the resuming run selected.
- Storing runs on a worker thread that stops at the next chunk on cancellation, and reports storing progress (previously collected and discarded).

## Security review

An independent adversarial review (malicious relay, malicious peer holding the PIN, local misuse) found the cryptographic core sound — no nonce reuse, key or role confusion, reflection, splicing, plaintext fallback or offline PIN test — and these problems around it, fixed here:

- **Terminal injection.** Relay notices are unauthenticated: they are now shown as fixed local text by status, never as their body. Peer text (device names, error messages, acks) is stripped of control characters and bounded; HELLO fields are type- and format-checked, and a malformed greeting ends the handshake.
- **Concurrent downloads.** Two receiving runs shared one buffer file, so one peer's data could be imported into another run's workspace. Each run now downloads into a file of its own in the data directory (0600 where files carry permission bits); only a download whose storing failed is published as the resumable buffer.
- **URL fetches from synced data.** Imported URL records could make `refresh-urls` fetch arbitrary addresses (e.g. cloud metadata). Only URLs recorded on this device are refreshed (`device.recorded_here`).
- **Stream validation.** Every line of a data stream is checked (one meta with vectors, batches of record objects with vectors, done) and re-serialised into the buffer; vector-less memories are refused, so nothing received is embedded locally; a phase runs at most once per session; receive queues are bounded.
- **Relay limits.** 80 MiB message cap, a taken role is refused instead of replaced, unknown roles are rejected, writes have a deadline, and only unpaired rooms expire (a long transfer is no longer cut at one hour).
- **CSRF.** `/api/sync/serve` was a GET a cross-site page could trigger; it is now a POST with a JSON body.
- **Mixed versions.** A protocol 1 consumer sent the PIN in clear before it could learn the provider was newer, so a malicious relay could use it to complete a protocol 2 session in its place. The one-piece share code closes this: protocol 1 clients cannot accept it.
- **Default join pushed.** A default (two-way) join sent the whole selected workspace to whoever issued the room and PIN, without showing who that was. Joining is now receive-only, and a push first names the receiving device and waits for a yes.
- **Errors and files.** Every key-exchange decoding failure becomes a clean 400; unreadable buffers ask for `--fresh` instead of crashing; download files are 0600 on Linux and macOS; the peer receives generic failure text instead of local exception details.

Open:

- The share code is passed on the command line (visible to other local users briefly, and in shell history); it is single-use, so a leaked code from history is worthless once the session ends. Device trust remains keyed by a non-secret fingerprint; it never replaces the PIN, which is why the PIN must stay long.

## Invalidation conditions

- If the pinned `spake2` package becomes unmaintained, or SPAKE2 or its implementation is found weak, revisit the PAKE; a mature Python CPace implementation would then be a candidate.
- If one side of a sync must run without the Python runtime, for example a Go-only or mobile client, the single Python implementation must be revisited.
- If share codes must become shorter or reusable, the one-guess-per-session argument no longer holds and authentication must be re-derived.

## Validation

- `tests/test_sync_live.py` (42 tests) runs both sessions through a real WebSocket relay that records every frame: two-way sync with the PIN, secret content, hostname, fingerprint and workspace name absent from all relay frames and only two cleartext frames; wrong PIN; reject, unknown decision, trust and auto-accept; altered, dropped, replayed and injected frames; protocol 1 consumer and provider; every direction combination, refusal of a request outside the grant, new-workspace creation; the receive-only default, the serving device shown before a push, declining a push (the pull still runs; a send-only join ends with nothing to sync); resumable buffer kept on failure, resumed, cleaned up on success, and release-era buffers; CLI serve/join transcripts and exit codes; hostile peers (escape-laden or malformed greetings, repeated requests, vector-less memories, malformed stream headers), forged relay notices, concurrent receiving runs, unreadable buffers; share-code and relay parsing; key agreement, room binding, direction separation and malformed messages. A mutation that turns the cipher into a no-op fails the encryption, tamper, drop, replay and wrong-PIN tests.
- Full Python suite (134 tests), Go `vet`/`test` with the race detector (relay tests in `cmd/relay_test.go`, including taken-role, unknown-role and oversized-message refusals), dashboard `tsc`, `eslint`, static build and 12 Playwright tests (the one-string share code, the receive-only default, and the two-way confirmation naming the serving device).
- Process-level run: two data directories with separate Qdrant servers, the Go relay and the built `imprint` binary. A send-only curated workspace (1,200 memories with 1024-dim vectors and a fact) was received into a new workspace while the provider's other workspace stayed private; a second two-way run auto-accepted the trusted device and deduplicated everything; a wrong PIN exited 1 on both sides; Ctrl+C exited 130 while waiting and at the approval prompt.
- The same run with the share code through the public relay (`wss://imprint.alexandruleca.com`, behind Cloudflare's proxy): a default join received the send-only workspace (1,200 memories and a fact) and sent nothing; a `--two-way` join named the serving device, asked, and after yes deduplicated both ways; declining still pulled; a wrong PIN exited 1 on both sides; the old `--pin` syntax exited 2. Frames of 63 MiB and a connection idle for 130 s (past the proxy's 100 s idle cut; client pings every 20 s) passed through the proxy.
- Not run: native Windows/macOS, Python 3.12 (tests ran on 3.14).
