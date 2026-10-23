---
title: ADR 0016 — Sync direction and workspace sharing
description: Per-machine send/receive policies negotiated in the sync handshake, per-session workspace selection and non-destructive workspace copies.
---

- Status: Accepted; implementation details superseded by [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md)
- Date: 2026-10-23
- Related: ADR 0006, ADR 0010, ADR 0015, ADR 0018

> The direction policies, grant, plan and workspace selection below stand. ADR 0018 replaced the two implementations with one, encrypted the session end to end, and removed the compatibility rules for older peers: a request outside the grant now gets 403 and ends the session (no draining), `/sync/status` is gone, the dashboard can use any relay, and the resume buffer records its workspace.

## Context

Users want to curate part of their memory into a separate workspace and share only that with another device or a colleague, and to sync one way when two-way merging is unwanted. Live sync could do neither. The joining side always pulled and then pushed (`cmd/sync.go` `syncPull`, `imprint/sync_ws.py` `receive_session`), so the serving machine always imported whatever the peer sent. Both sides always used the active workspace. `imprint migrate` deleted the selected memories from the source (`imprint/cli_migrate.py`), so curating a share workspace stripped memories from one's own. The provider's handshake reply carried only `"ok"`, giving the joining side no way to learn what the provider would accept.

## Decision

**Direction policy.** Each machine states what it is willing to do: send (its memories may leave it) and/or receive (peer memories may be imported). CLI flags `--send-only` / `--receive-only` (mutually exclusive; neither means both) apply to `imprint sync serve` and `imprint sync <target>`; the API and dashboard use `direction` = `both` | `send` | `receive`. Go (`syncPolicy`) and Python (`SyncPolicy`) each have one representation used by every path.

**Handshake grant.** The provider's 200 reply body becomes `{"pull": send, "push": receive}`: what the consumer may request. A consumer treats any non-object body, such as an older provider's `"ok"`, as both allowed.

**Consumer plan.** The consumer runs `pull = grant.pull AND local receive` and `push = grant.push AND local send`, reports the plan and skipped phases (CLI `Plan:` line, SSE `plan` event), and fails with `nothing to sync: …` when no phase remains. A send-only consumer never processes the local resume buffer, which holds a pull, and leaves it untouched; `--fresh --send-only` is rejected. Unknown sync flags are errors, so a typo cannot silently become a two-way sync.

**Provider enforcement.** The provider enforces its policy whatever the consumer requests, identically in Go and Python. A refused pull gets a stream line `{"kind":"error","message":"provider is serving receive-only: pulls are refused"}`, which existing importers already treat as a failure. A refused push is read and discarded until `done`/`error`/close, importing nothing, and answered with status 403 so consumers that push unconditionally still receive their ack and the request stream stays in step. A receive-only Go provider also refuses `/sync/status`, since counts and project names are data. Providers announce their mode when serving.

**Workspace per session.** `--workspace NAME` (API/dashboard: `workspace`) selects the workspace without changing the active one; the default is the active workspace captured at start. The Go CLI pins `IMPRINT_WORKSPACE` for the Python helpers it spawns. The long-running API process passes the workspace explicitly through `stream_export_lines` / `process_import_buffer` into the vector store and graph, never through process-global state. Serving requires an existing workspace. A joining machine may name a new one, registered without switching just before pulled data is stored; a plan that only sends requires an existing workspace.

**Copy migration.** `imprint migrate … --copy` copies instead of moving; the CLI default stays move because the command is named migrate, and the dashboard defaults to Copy because it is non-destructive. Copied knowledge-graph facts skip rows the target already holds, using sync's `(subject, predicate, object, valid_from)` key, so repeated curation does not duplicate facts. A real migration registers its target workspace, which a CLI-curated workspace needs before it can be served; a dry run changes nothing, including target creation.

## Alternatives

- *Let the consumer pick the direction alone.* The serving machine would still import whatever an old or misconfigured client pushed, so the provider must enforce its own policy.
- *Close the connection on a refused push.* An older consumer would see a broken connection instead of a clear 403, and the provider loses the session.
- *Separate `pull-only`/`push-only` commands or a second endpoint.* That would duplicate the protocol path; one policy and an intersection keep a single flow.
- *A `--direction` CLI flag.* The two flags read naturally on both sides, and the API's `direction` string maps onto the same policy.
- *Set `IMPRINT_WORKSPACE` in the API process.* That would leak one session's workspace into concurrent requests and dashboard switches.
- *Export a filtered subset directly over sync.* Curating into a workspace keeps one share unit that can be inspected, searched and re-synced, and it reuses existing isolation.
- *Make copy the CLI default.* That would silently change an existing command's meaning for scripts.

## Consequences

One-way sharing is possible without exposing or modifying the rest of a user's memory, and a provider can act as a read-only source or a write-only drop box. Pre-change consumers keep working against two-way providers. Against a send-only provider their push receives 403 (an older CLI prints it after "Pushed local → remote"). Against a receive-only provider their pull fails and they stop before pushing, so pushing into a receive-only provider needs an updated joining client. New consumers interoperate with older providers as two-way.

Sync and copy never delete, so memories removed from a curated workspace remain on machines that already received them. Shared workspaces still require matching embedding profiles (ADR 0010). The relay sees plaintext frames, including the PIN and the data. This decision does not add end-to-end encryption; documentation now says so and recommends a self-hosted relay. The dashboard still connects only to the default relay. The resume buffer remains a single file processed into whichever workspace the resuming run selects. Migration writes bypass ADR 0015's purge exclusions, as before this change.

## Validation

- `go test ./cmd/...`: grant carried in the handshake for every policy; legacy `"ok"` and parsing of non-object bodies; the plan intersection, including nothing-to-sync; provider refusal of pull (error line), of push (drain, 403, next request still served) and of status; flag parsing, including mutual exclusion, unknown flags and `--fresh` with `--send-only`; workspace pinning and registration.
- `python -m unittest tests/test_sync_direction.py`: the same policy, plan and wording in Python; both dashboard providers refusing against a scripted relay; a consumer pulling into a newly registered workspace while the active one is unchanged; legacy `"ok"`; explicit-workspace export/import on an in-memory Qdrant; copy keeping the source and repeating without duplicate facts; a dry run creating nothing; API validation.
- Playwright: the sync page workspace and direction controls, keyboard radio selection, the plan, refusal and creation notices, and the migrate dialog defaulting to Copy.
- Not verified: a live sync through a real relay between two machines, and native platform runs.
