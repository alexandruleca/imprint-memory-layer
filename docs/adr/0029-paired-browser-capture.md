---
title: ADR 0029 — Paired local browser capture prototype
description: A workspace-bound Chrome extension capture API, explicit previews, and reuse of the serialized inline ingestion pipeline.
---

- Status: Accepted (WIP implementation on `wip/chrome-extension`; not a released integration)
- Date: 2026-10-23
- Extends: [ADR 0024](0024-enforce-local-service-boundaries.md) and [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md).
- Gmail availability and unpacked-only distribution are superseded by [ADR 0030](0030-chrome-extension-release-zip.md); the original rationale below is preserved.

## Context

A browser sees authenticated content that Imprint's URL fetcher cannot retrieve
from a URL alone. The dashboard API intentionally rejects extension origins,
and its general commands include filesystem/configuration/destructive actions.
Allowing every extension origin would widen that boundary. An experimental
capture client should reuse local storage, source replacement and queue locking
without granting the captured website access to local commands or credentials.

## Decision

Implement a development-only unpacked Manifest V3 extension with click-to-preview
and explicit save. Use `activeTab`/`scripting` for page extraction, trusted local
extension storage for credentials, and an extension worker that accepts messages
only from its own popup/settings. It contacts only literal `127.0.0.1` HTTP
addresses, refuses redirects, and requests no cookies or persistent site access.

Add a separate `/api/browser` router under the existing local API. Keep peer/Host
validation and ordinary dashboard origin rules. On browser routes require the
paired extension ID plus a random Bearer token; validate an exact extension
Origin when supplied. Preflight approval is origin-specific and does not grant
authenticated access. Pair through a local Python command, bind one existing
workspace, store only token hashes in a separate SQLite database, and support
rotation/revocation. The local API's existing trust of local native clients is
unchanged; these credentials do not claim isolation from hostile local software.

Queue bounded inline text/metadata as an internal `browser-capture` command.
The existing dispatcher launches the same interpreter with a queue job ID;
content/tokens are not process arguments. The worker takes the shared queue lock,
rechecks its pairing and uses `store_inline_content`. Pin workspace at submission.
Status returns only a pairing's own capture job state, not raw job bodies or logs.
Reuse existing vector fields and `doc_metadata`; add no vector collection schema
migration. Queue history retains raw capture content under existing local data
directory access rules. Browser records have `source_type=browser` and do not
participate in unauthenticated URL refresh.

Keep valid short inline captures/notes as a single chunk below the file chunker's
100-character minimum, honoring inline ingestion's existing 10-character floor.

Use URL-based page identities, separate content-addressed selection identities,
and Gmail account/message identities. Preserve SPA URL fragments. Gmail capture
only saves expanded message bodies with stable DOM message IDs, never a whole
partially rendered thread as a replacement source. Require the account email
explicitly in settings to avoid relying on mutable `/u/0` account slots. This
is user-supplied provenance and must match the tab. PDF/editor export adapters
and mailbox OAuth synchronization remain future work.

## Alternatives

Sending URLs alone fails for signed-in applications. Granting extension origins
general dashboard access exposes unrelated commands. Native messaging can avoid
HTTP permissions but adds an OS-specific host registration/install lifecycle;
defer it until the prototype establishes useful capture flows. Gmail API OAuth
is more suitable for comprehensive mailbox synchronization but requires a
different permission/verification lifecycle. Automatic all-site capture requires
broader permissions and data retention controls and is outside this first slice.

## Consequences

Users must run this branch's backend, load unpacked source and pair manually.
One token grants one workspace; changing workspaces rotates the pairing. Existing
memories are preserved on revocation. Revocation prevents future/queued ingestion
but does not stop an already executing job. A multi-document job can partly index
before failure. Unchanged source chunks retain existing capture metadata. Gmail
selectors, browser permissions and account identity need live validation before
release. No claims of Google Docs, PDFs, real Gmail, Store approval or native
Windows/macOS Chrome validation follow from synthetic Chromium tests.

## Validation

Python regressions cover exact origins, peer/Host checks, credential rotation,
workspace/job isolation, bounded inputs, source identities, revocation before
execution, shared lock release and real dispatcher/subprocess failure reporting.
Node checks cover loopback URLs, trusted senders and transport settings. The
disposable Chromium smoke covers actual extension loading/pairing/storage and
save/status plus synthetic article, selection and expanded Gmail extraction.
Executed results and remaining limits belong in [platform validation](../platform-validation.md).
