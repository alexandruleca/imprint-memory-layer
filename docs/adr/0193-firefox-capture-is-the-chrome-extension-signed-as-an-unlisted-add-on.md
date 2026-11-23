---
title: ADR 0193 — Firefox capture is the Chrome extension, signed as an unlisted add-on
description: Build the Firefox add-on from the Chrome extension's source, test it in a pinned Firefox ESR, and have addons.mozilla.org sign stable releases without listing them.
---

- Status: Amended
- Date: 2026-11-20
- Amends: [ADR 0030](0030-chrome-extension-release-zip.md) — releases also carry a Firefox add-on, and both packages take the release's version
- Amended by:
  - [ADR 0203](0203-the-firefox-add-on-is-listed-on-addons-mozilla-org.md) — stable releases submit a listed version to addons.mozilla.org instead of signing an unlisted one, and carry only the unsigned add-on (since superseded by ADR 0206)
  - [ADR 0206](0206-the-firefox-add-on-goes-back-to-unlisted-signing.md) — a signing that AMO throttles for longer than 20 minutes ships the release with only the unsigned add-on instead of failing it (since superseded by ADR 0207)
  - [ADR 0207](0207-the-firefox-add-on-is-listed-on-addons-mozilla-org-again.md) — stable releases submit a listed version to addons.mozilla.org again and carry only the unsigned add-on
- Related: [ADR 0192](0192-the-capture-api-accepts-firefox-extension-origins.md), [ADR 0185](0185-local-chrome-capture.md), [ADR 0191](0191-chrome-full-page-screenshots-are-stitched-in-the-popup.md)

## Context

The user asked for the capture extension in Firefox as well. Release Firefox installs
an add-on permanently only when Mozilla has signed it; unsigned add-ons load until
restart, or in Developer Edition and Nightly with signature checks turned off. The
user chose signed, self-hosted distribution over a public listing on
addons.mozilla.org (AMO) or unsigned builds.

A probe of the Chrome extension in Firefox ESR 153.7 through geckodriver found few
differences:

- Firefox runs a Manifest V3 background as an event page (`background.scripts`), not a
  service worker, and wants `options_ui` and an add-on ID.
- `storage.local.setAccessLevel` does not exist, and calling it stopped the background.
- `runtime.id` is the add-on ID while pages live at `moz-extension://<per-install UUID>/`
  ([ADR 0192](0192-the-capture-api-accepts-firefox-extension-origins.md)).
- Injected capture functions, promise results, `OffscreenCanvas` and a same-origin
  fetch with the tab's cookies behave as in Chrome; `captureVisibleTab` needs the
  `activeTab` grant of a toolbar click, as in Chrome.

## Decision

- **One source.** `extensions/chrome` stays the only source. The two Chrome-only calls
  are feature-checked, and the sender check compares against `runtime.getURL("")`.
  `scripts/package_chrome_extension.py --browser firefox` writes the Firefox manifest:
  background `scripts` as a module, `options_ui`, the add-on ID
  `capture@imprintmcp.alexandruleca.com`, `strict_min_version` 140.0, and
  `data_collection_permissions: none`, because captures go only to Imprint on the same
  device. Permissions and content security policy are the Chrome extension's. The
  archive holds the files at its root, without Chrome's `INSTALL.txt`.
- **Versions.** AMO signs each version once, so both packages take the release tag as
  their version: `v0.57.1` becomes 0.57.1, and `v0.57.1-dev.2` becomes 0.57.1.2, with
  Chrome showing the tag through `version_name`.
- **Testing.** CI and the release job run `extensions/chrome/tests/firefox-smoke.mjs`
  against the packaged add-on. It uses Firefox ESR 153.7.0esr and geckodriver 0.37.1,
  pinned by SHA-256 in `scripts/fetch-firefox.sh`, and drives them through WebDriver
  with no npm dependency. WebDriver refuses to navigate to `moz-extension://` pages, so
  the test opens them from the browser's own context (`--allow-system-access`).
- **Signing.** For a stable release, when the `AMO_JWT_ISSUER` and `AMO_JWT_SECRET`
  repository secrets exist, `scripts/sign_firefox_extension.py` uploads the add-on to
  AMO's v5 API as unlisted, creates the add-on or a new version, waits for the
  signature and downloads it. A version AMO has already signed is downloaded again,
  so a rerun does not fail. The release then carries `imprint-firefox-extension.xpi`
  and drops the unsigned one. Prereleases, and releases made before the secrets
  exist, carry `imprint-firefox-extension-unsigned.xpi`. A signing failure fails the
  release.

## Rejected alternatives

- **Public listing on AMO.** It brings automatic updates and discovery, but each version
  waits for human review and needs a listing page and privacy text. The user chose
  self-hosted signing.
- **Unsigned add-ons only.** Release Firefox would remove them at restart.
- **`web-ext` for signing and testing.** It is an npm toolchain outside the hashed lock
  files for two API flows; the standard-library signer is under 200 lines and tested
  against a stand-in for the API.
- **A separate Firefox source tree.** The differences fit in the manifest and three
  feature checks; two trees would drift.
- **Playwright's Firefox.** Its Firefox build cannot load extensions.

## Consequences

- Chrome's ZIP now shows the Imprint release's version (0.57.x) instead of the source
  manifest's 0.4.0, which remains for unpacked development loads.
- An unlisted add-on has no update channel: users install each release's `.xpi` again.
  Hosting an update manifest (`update_url`) would add automatic updates.
- The add-on ID is permanent on AMO once the first version is signed.
- `data_collection_permissions: none` is a declaration to Mozilla that the add-on sends
  data only to the user's own Imprint on the same device. A workspace's sync and model
  settings still apply after that, as for Chrome.
- Firefox has no access-level control for `storage.local`. The only code the add-on
  injects into pages is its own capture functions, which do not read it.
- Signing is untested against the real AMO until the secrets are set.

## Invalidation conditions

- If AMO retires or changes its v5 signing API, update the signer or move to Mozilla's
  supported tool.
- If Firefox gains storage access levels, use them as Chrome does.
- If users need automatic updates, host an update manifest or list the add-on on AMO,
  in a new record.
- If Playwright's Firefox gains extension support, the WebDriver smoke can move to it.

## Validation

- Linux x64 (WSL2), Node 22.23.5: the Firefox smoke test passed against the packaged
  add-on in Firefox ESR 153.7.0esr with geckodriver 0.37.1. It covered discovery,
  workspace choice, signed-in page, selection and file capture, full-page stitching,
  upload, search handoff and bridge isolation. The Chrome smoke test and the
  extension's Node tests passed on the same tree.
- `tests/test_chrome_extension_package.py` checks the Firefox manifest, file set,
  release-version mapping and Chrome version stamping.
- `tests/test_sign_firefox_extension.py` signs against a stand-in AMO: a new add-on, a
  new version, a version already signed, a validation failure and a download with no
  signature, checking every request's JWT.
- Not run: signing against the real AMO, a toolbar click in headed Firefox, and Firefox
  on Windows or macOS.
