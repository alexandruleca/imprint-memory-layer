---
title: ADR 0030 — Chrome preview ZIP and release downloads
description: Publish a minimal manual-install extension ZIP with validated releases, keep Gmail and document capture coming soon, and expose the newest available ZIP on the website.
---

- Status: Accepted
- Date: 2026-10-23
- Extends: [ADR 0002](0002-build-and-validate-before-publication.md), [ADR 0014](0014-main-only-release-packaging.md), [ADR 0029](0029-paired-browser-capture.md).
- Supersedes ADR 0029's initial availability of Gmail in the extension UI; the pairing and storage architecture remains accepted.

## Context

The initial WIP extension advertised Gmail even though only synthetic selectors
had been exercised. The requested first download should offer working page and
selection capture, with planned integrations clearly marked Coming soon. Users
also need a distributable ZIP and installation instructions without cloning the
repository or receiving development files or private browser state.

## Decision

Gate Gmail and Google Docs/Sheets before page or selection extraction; label PDF
capture Coming soon. Remove Gmail account setup from the shipped settings UI.
Retain the backend's message identity scaffold for future work. Display coming-soon
integrations in the popup and on a dedicated `/chrome-extension/` website page.

Build `imprint-chrome-extension.zip` from a fixed runtime-file allowlist, with a
single folder containing `manifest.json` and standalone `INSTALL.txt`. Use fixed
ZIP timestamps/permissions for reproducible bytes, atomic output replacement and
a local checksum sidecar. Exclude tests, dependency manifests, development
dependencies and local state. Users extract the ZIP and use Chrome Developer
mode → Load unpacked. Updating in the same folder preserves the unpacked ID.

Add a required main-only release job that tests packaging, builds the ZIP and
loads the extracted artifact in disposable Chromium. Publish it with the complete
native asset set and include it in `SHA256SUMS`; existing native validation gates
remain required. Local builds and temporary packaging fixtures are allowed on
branches, with no branch release publication.

The website uses its existing build-time release manifest to choose the newest
stable release that actually contains this asset. A newer desktop-only release
must not create a broken extension download. Before the asset exists, show a
release-list link instead of claiming an available ZIP. The existing successful
release → Pages workflow refreshes the download without a separate deployment
service. The page includes manual install, pairing, update and data-flow guidance
and reuses the site's layout, colors, type and icons.

## Alternatives

A Chrome Web Store release would avoid Developer mode but needs a separate
publication/review process. A signed CRX does not provide a universally usable
manual installation path for ordinary Chrome users. Zipping the whole source
tree risks distributing tests, dependencies or local state. Hardcoding a version
URL gets stale; blindly pointing at latest/download can fail when an otherwise
valid newer release omits the extension.

## Consequences

The extension remains a preview and requires a browser-enabled backend, shipped
in the matching release. Older installed backends need an update. Gmail and
document integrations have no announced delivery date. A small extra release job
is required before publication. Linux Chromium artifact validation does not
establish native Windows/macOS Chrome installation or Store approval.

## Validation

Packaging regressions check the exact file set, absence of injected credentials
and dependencies, deterministic output, checksum contents and preservation of a
previous artifact when a runtime file is missing. The browser smoke uses the
extracted ZIP for pairing/save/status, validates page/selection capture and
Gmail/Docs coming-soon gates, and preserves credential boundary checks. Website
tests cover stable asset selection, missing-asset fallback and responsive page
rendering. Executed results belong in [platform validation](../platform-validation.md).
