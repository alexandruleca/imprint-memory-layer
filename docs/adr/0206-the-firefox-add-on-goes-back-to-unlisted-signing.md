---
title: ADR 0206 — The Firefox add-on goes back to unlisted signing
description: Stable releases again have addons.mozilla.org sign the Firefox add-on as unlisted and attach it, and the website offers it; the listed submission and the AMO listing tooling are removed, and a signing that AMO throttles for long ships the release with only the unsigned add-on.
---

- Status: Accepted
- Date: 2026-11-23
- Supersedes: [ADR 0203](0203-the-firefox-add-on-is-listed-on-addons-mozilla-org.md)
- Amends: [ADR 0193](0193-firefox-capture-is-the-chrome-extension-signed-as-an-unlisted-add-on.md) — a signing that AMO throttles for longer than 20 minutes ships the release with only the unsigned add-on instead of failing it
- Related: [ADR 0202](0202-stable-releases-submit-the-extension-to-the-chrome-web-store.md) (the Chrome Web Store, unchanged), [ADR 0192](0192-the-capture-api-accepts-firefox-extension-origins.md), [browser extension](../browser-extension.md#firefox)

## Context

- **The listed route never started.** ADR 0203 needed the first listed version uploaded by hand in the Developer Hub ("On this site"). On 2026-11-22 the owner uploaded 0.59.0.1 there by mistake as unlisted. After that, AMO refused uploads with "You have submitted too many uploads recently". On 2026-11-23, about 12 hours later, it still refused them.
- **What releases did meanwhile.** v0.61.0 and v0.61.1 carried only `imprint-firefox-extension-unsigned.xpi`. Their `firefox-listing` jobs submitted nothing: v0.61.1's job warned that the add-on "has no listing on addons.mozilla.org yet". AMO holds the add-on's unlisted versions 0.58.0, 0.59.0, 0.59.0.1 and 0.60.0, and no listed version. The website offered v0.60.0's signed file, the newest stable release that had one.
- **The request.** The owner asked to stop the listed submission and "keep mozilla on the regular track hosted on the imprint site and that's that for now".
- **Throttling reaches CI too.** The `amo-listing` runs on 2026-11-22 were throttled after about three writes a minute, then refused for 2,403 seconds after about seven (ADR 0203). The owner's uploads were refused for hours. Under ADR 0193, a signing failure fails the release, and `publish` needs the `firefox-extension` job. So a long throttle on the add-on would block every desktop package of that release.

## Decision

- **Sign unlisted again.** For a stable release, the `firefox-extension` job runs `scripts/sign_firefox_extension.py` with the existing `AMO_JWT_ISSUER` and `AMO_JWT_SECRET` secrets. The script does what it did under ADR 0193: upload to the unlisted channel, create the version, wait for Mozilla's signature and download it. The release attaches `imprint-firefox-extension.xpi` in place of the unsigned file. The website's **Firefox add-on** button offers the newest stable release that has a signed file; its code is unchanged.
- **Throttling.** The client keeps the retry from ADR 0203. It waits out an HTTP 429 when the wait ends within its 20-minute deadline (the Retry-After header, else the "available in N seconds" detail, plus a second). When AMO asks for a longer wait, the script prints a warning and writes no signed file. The release then ships `imprint-firefox-extension-unsigned.xpi`, as a prerelease does, and the website keeps offering the previous signed file. Any other failure (validation, a disabled file, a download without a Mozilla signature) still fails the release.
- **A number taken in the other channel fails.** If AMO already holds the version number as listed, the signing fails, because AMO takes each number once. It does not download that file.
- **The listed machinery is removed.** That covers the `firefox-listing` job, the `amo-listing` workflow, `scripts/update_amo_listing.py` and its tests, and `extensions/store/` (the listing's text, privacy policy and screenshots). They remain in history at commit 301459a. `scripts/publish_firefox_extension.py` returns to its earlier name, `scripts/sign_firefox_extension.py`.

## Rejected alternatives

- **Keeping the listed route and waiting out the throttle.** The owner asked to stop. The first listed version still needs a manual upload, and AMO had refused uploads for about 12 hours when they asked.
- **Unlisted signing with a hosted update manifest (`update_url`).** It would bring automatic updates without a listing, as ADR 0193 and 0203 noted. Every release would also have to publish an `updates.json` that the site serves. The owner asked for the self-hosted add-on and nothing more "for now".
- **Keeping the listing tooling unused.** An add-on without a listed version has no public AMO page, so the listing content has nowhere to show. Workflow code that never runs drifts, and git history keeps it.
- **Failing the release on any throttle, as ADR 0193 did.** A throttle measured in hours would hold back the desktop packages for the sake of a browser add-on. The website still has the previous signed file to offer.

## Consequences

- Firefox users install from the website or the release's file. As under ADR 0193, the add-on does not update itself.
- v0.61.0 and v0.61.1 have no signed add-on. The website offers v0.60.0's until the next stable release is signed. That release's number (0.61.2 or higher) is one AMO has not used.
- The listing text and five screenshots that `amo-listing` applied on 2026-11-22 stay in the add-on's AMO record. Nothing shows them.
- A release whose signing was throttled has no signed add-on. Rerunning its `firefox-extension` job does not reach the published release; the next stable release signs.
- The Chrome Web Store job (ADR 0202) is unchanged.

## Invalidation conditions

- If Firefox users need automatic updates, list the add-on on AMO (the removed tooling is at commit 301459a) or host an update manifest, in a new record.
- If AMO's throttle leaves stable releases unsigned more than occasionally, sign in a separate job after `publish` that can be rerun and attaches its file to the published release.
- If AMO changes or retires its v5 API, update the script or move to Mozilla's supported tool.

## Validation

- Linux x64 (WSL2), Python 3.14: `tests/test_sign_firefox_extension.py` ran seven tests against a stand-in AMO that checks every request's JWT, all passing with ResourceWarnings as errors. Four are restored from ADR 0193: the first release creates the unlisted add-on, a later release adds a version, a signed version is downloaded without uploading again, and a validation failure or a file without a signature writes nothing. Three are new:
  - a number AMO holds as listed is refused without uploading;
  - a throttle of a few seconds is waited out and the upload goes through;
  - a throttle of 4,000 seconds raises `Throttled`, and `main` prints a warning and writes no signed file.
- The workflow's job list was read back from the YAML: `firefox-listing` is gone and `chrome-web-store` remains.
- Not run: signing against the real addons.mozilla.org. That happens with the next stable release.
