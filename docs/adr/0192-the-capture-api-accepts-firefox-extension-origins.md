---
title: ADR 0192 — The capture API accepts Firefox extension origins
description: Let the local capture routes take a Firefox add-on's per-install UUID and its moz-extension origin, held to the same exact match as a Chrome extension ID.
---

- Status: Accepted
- Date: 2026-11-20
- Amends: [ADR 0185](0185-local-chrome-capture.md) — the local routes also accept a Firefox extension's UUID and `moz-extension://` origin
- Related: [ADR 0193](0193-firefox-capture-is-the-chrome-extension-signed-as-an-unlisted-add-on.md), [ADR 0024](0024-enforce-local-service-boundaries.md)

## Context

ADR 0185 requires each request on `/api/browser/local/` to name a Chrome extension
ID (32 letters a–p) in `X-Imprint-Extension-Id` and, on a submission, to carry
exactly `Origin: chrome-extension://<that ID>`. Job status is readable only by
the ID that submitted the job.

A Firefox build of the same extension ([ADR 0193](0193-firefox-capture-is-the-chrome-extension-signed-as-an-unlisted-add-on.md))
cannot meet that. In Firefox, `runtime.id` is the add-on's ID
(`capture@imprintmcp.alexandruleca.com`), but its pages live at
`moz-extension://<UUID>/`, where the UUID is random for each installation. A probe
in Firefox ESR 153.7 through geckodriver showed that a POST from the add-on carries
`Origin: moz-extension://<UUID>` and no cookies, that a GET carries no Origin, and
that no preflight is sent while the add-on holds its `http://127.0.0.1/*` host
permission.

## Decision

The extension sends the host of its own origin (`new URL(runtime.getURL("")).host`)
as `X-Imprint-Extension-Id`: the extension ID in Chrome, the UUID in Firefox.

The local routes accept either form. A Chrome ID (32 letters a–p) must come with
`chrome-extension://<ID>`, and a lowercase UUID with `moz-extension://<UUID>`. A
submission must carry exactly that Origin; any Origin present on another request
must match it too. A preflight takes the ID from either scheme. Jobs belong to the ID
that submitted them, as before. The paired routes of ADR 0029 share the ID check;
no Firefox client pairs.

## Rejected alternatives

- **Send the add-on ID and accept any `moz-extension://` origin.** The add-on ID never
  appears in the origin, so the server could not tie a submission to the ID it names,
  and any add-on could read another's job status by claiming its ID. Matching the UUID
  to the Origin, which the browser sets, gives Firefox the same per-installation
  binding Chrome has.
- **Treat the UUID like Chrome's ID only in the extension, leaving the server
  Chrome-only.** Every Firefox submission would fail the origin check.

## Consequences

- Firefox gets the same boundary as Chrome. ADR 0185 already allows any installed
  extension with loopback access to use the capture routes; this record does not
  widen that.
- Removing and reinstalling the add-on gives it a new UUID, so the previous
  installation's last job can no longer be read. The popup clears that job instead of
  showing an error.
- A GET without Origin, as in Chrome, proves nothing about the ID it names.

## Invalidation conditions

- If Firefox stops sending Origin on add-on submissions, Firefox captures fail the
  check; decide how to bind them in a new record.
- If Firefox makes extension origins derivable from the add-on ID, revisit the header.

## Validation

- `tests/test_browser_capture.py` submits and reads a job with a Firefox UUID and its
  origin, answers its preflight, and rejects another UUID's origin, the Chrome scheme
  with a UUID, the Firefox scheme with a Chrome ID, an upper-case UUID and the add-on
  ID itself.
- The Firefox smoke test asserts the header and Origin of real submissions from the
  packaged add-on in Firefox ESR 153.7 (Linux).
