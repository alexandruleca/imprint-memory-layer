---
title: ADR 0203 — The Firefox add-on is listed on addons.mozilla.org
description: After a stable release is published, CI submits its add-on as a listed version to the add-on's addons.mozilla.org listing for Mozilla's review, so Firefox updates installed copies; releases stop carrying a self-signed add-on.
---

- Status: Accepted
- Date: 2026-11-22
- Amends: [ADR 0193](0193-firefox-capture-is-the-chrome-extension-signed-as-an-unlisted-add-on.md) — stable releases submit a listed version to addons.mozilla.org instead of signing an unlisted one, and carry only the unsigned add-on
- Related: [ADR 0202](0202-stable-releases-submit-the-extension-to-the-chrome-web-store.md) (the Chrome counterpart), [ADR 0192](0192-the-capture-api-accepts-firefox-extension-origins.md), [browser extension](../browser-extension.md#store-publishing)

## Context

- **The request.** On 2026-11-22 the owner asked for automatic updates and store approval for Chrome "like we do for firefox". Releases did not do that for Firefox. Under ADR 0193 they signed the add-on as unlisted, which has no update channel. ADR 0193's own invalidation condition says: if users need automatic updates, list the add-on on AMO or host an update manifest, in a new record. Offered the choice, the owner chose to switch to a listed add-on.
- **What AMO holds.** Releases v0.58.0 and v0.59.0 signed the add-on `capture@imprintmcp.alexandruleca.com` as unlisted versions 0.58.0 and 0.59.0.
- **The owner's submission.** The owner was submitting the add-on in the Developer Hub. AMO's validator showed two warnings, and commit 4daa3bf fixed both: Firefox for Android's own minimum version, and an `innerHTML` assignment.
- **What the v5 API allows.** AMO's API documentation was read on 2026-11-22:
  - An upload names its channel.
  - A listed version needs a license and an add-on with a name, summary and categories. "Add-on properties cannot be set with version create", and a version without a license inherits the previous version's.
  - Version detail by `v<number>` covers every channel and names the version's `channel`. Add-on detail lists an author's `channels`.
  - A file's status is `public` (approved), `unreviewed` (awaiting review) or `disabled` (rejected, disabled or not reviewed).
  - The documentation does not give the request format for categories or translated fields, or when a listed file is signed.

## Decision

- **When.** A new `firefox-listing` job runs after `publish`, for stable releases only, with the existing `AMO_JWT_ISSUER` and `AMO_JWT_SECRET` secrets. Without them it warns and submits nothing.
- **The listing is made once by hand.** The owner uploads one version in the Developer Hub with **Upload New Version → On this site** and completes the listing there (summary, categories, license, screenshots), just as the Chrome Web Store item was made by hand.
- **The submission.** `scripts/publish_firefox_extension.py` replaces `scripts/sign_firefox_extension.py`:
  1. If AMO already holds this version as listed, it reports the file's status and stops.
  2. If AMO holds this version in another channel, the job fails, because a version number is taken once.
  3. Without a listing, it warns with how to make one and stops.
  4. Otherwise it uploads to the listed channel, waits for validation and creates the version. The license and listing carry over. A version AMO disables at once fails the job.
- **No waiting for the signature.** Mozilla signs and serves the approved file. The `firefox-extension` job no longer signs, and every release carries only `imprint-firefox-extension-unsigned.xpi`.

## Rejected alternatives

- **Keeping unlisted signing (ADR 0193).** It has no automatic updates, and the owner chose a listing.
- **Unlisted signing with a hosted update manifest (`update_url`).** ADR 0193 named this as the other route to updates. It was not put to the owner, who asked for store approval as on Chrome. It would also mean publishing an `updates.json` with every release.
- **Creating the listing from the script** (an add-on edit for the metadata, then a version with a license). The documentation does not give the request format for categories and translated fields. The listing's description and screenshots belong in the Developer Hub. Doing it by hand is a single step, like the Chrome item.
- **Waiting for the listed version's signature and attaching it to the release.** Review time has no bound, and AMO already serves the signed file.

## Consequences

- Copies installed from the listing update once Mozilla approves each version.
- It was not checked whether Firefox moves copies installed from the v0.58.0 or v0.59.0 files to listed versions. Reinstalling from the listing is the sure path.
- Releases after v0.59.0 carry no signed add-on. Until the listing exists, the website keeps offering v0.59.0's signed file: it picks the newest stable release that has one.
- The first listed version must be higher than 0.59.0 and a number AMO has not signed. A prerelease build must never be submitted by hand, because `v0.60.0-dev.1` is 0.60.0.1, which ranks above 0.60.0.
- Mozilla may reject a version. The job reports a disabled file as a failure, and the next release submits a new version.

## Invalidation conditions

- If AMO changes or retires its v5 submission API, update the script or move to Mozilla's supported tool.
- If Mozilla refuses or removes the listing, return to unlisted signing (ADR 0193) and consider a hosted update manifest, in a new record.
- If the API documents creating a listing with its required metadata, and the owner wants the first submission automated, the script may create it.

## Validation

- Linux x64 (WSL2), Python 3.14: `tests/test_publish_firefox_extension.py` ran four tests, all passing, against a stand-in AMO that checks every request's JWT. They covered:
  - a new listed version, with its upload's channel;
  - a version already listed;
  - no listing, with no channels or only `unlisted`;
  - a validation failure, a disabled file, and a number already signed as unlisted.
- addons-linter 10.16.0 reported no warnings on the add-on built from commit 4daa3bf.
- Not run: the real AMO listed submission. The listing does not exist yet, and the job waits for the next stable release.
