---
title: ADR 0203 — The Firefox add-on is listed on addons.mozilla.org
description: After a stable release is published, CI submits its add-on as a listed version to the add-on's addons.mozilla.org listing for Mozilla's review, so Firefox updates installed copies; releases stop carrying a self-signed add-on.
---

- Status: Superseded
- Date: 2026-11-22
- Superseded by: [ADR 0206](0206-the-firefox-add-on-goes-back-to-unlisted-signing.md)
- Amends: [ADR 0193](0193-firefox-capture-is-the-chrome-extension-signed-as-an-unlisted-add-on.md) — stable releases submit a listed version to addons.mozilla.org instead of signing an unlisted one, and carry only the unsigned add-on
- Related: [ADR 0202](0202-stable-releases-submit-the-extension-to-the-chrome-web-store.md) (the Chrome counterpart), [ADR 0192](0192-the-capture-api-accepts-firefox-extension-origins.md), [browser extension](../browser-extension.md#store-publishing)

## Context

- **The request.** On 2026-11-22 the owner asked for automatic updates and store approval for Chrome "like we do for firefox". Releases did not do that for Firefox. Under ADR 0193 they signed the add-on as unlisted, which has no update channel. ADR 0193's own invalidation condition says: if users need automatic updates, list the add-on on AMO or host an update manifest, in a new record. Offered the choice, the owner chose to switch to a listed add-on.
- **What AMO holds.** Releases v0.58.0, v0.59.0 and v0.60.0 signed the add-on `capture@imprintmcp.alexandruleca.com` as unlisted versions 0.58.0, 0.59.0 and 0.60.0. v0.60.0 was cut at 15:19 UTC from the merge before this change.
- **The owner's submission.** The owner was submitting the add-on in the Developer Hub. AMO's validator showed two warnings, and commit 4daa3bf fixed both: Firefox for Android's own minimum version, and an `innerHTML` assignment.
- **What the v5 API allows.** AMO's API documentation was read on 2026-11-22:
  - An upload names its channel.
  - A listed version needs a license and an add-on with a name, summary and categories. "Add-on properties cannot be set with version create", and a version without a license inherits the previous version's.
  - Version detail by `v<number>` covers every channel and names the version's `channel`. Add-on detail lists an author's `channels`.
  - A file's status is `public` (approved), `unreviewed` (awaiting review) or `disabled` (rejected, disabled or not reviewed).
  - The documentation does not give the request format for categories or translated fields, or when a listed file is signed.

## Decision

- **When.** A new `firefox-listing` job runs after `publish`, for stable releases only, with the existing `AMO_JWT_ISSUER` and `AMO_JWT_SECRET` secrets. Without them it warns and submits nothing.
- **The first listed version is uploaded once by hand.** The owner uploads it in the Developer Hub with **Upload New Version → On this site** and picks its license there, just as the Chrome Web Store item was made by hand.
- **The listing's content lives in the repository.** `extensions/store/` holds the summary, description, categories, homepage, support link, privacy policy and screenshots (1280×800, AMO's recommended size) with their captions. The `amo-listing` workflow applies them with `scripts/update_amo_listing.py` whenever they change on `main`, and on demand. Categories are written in the shape AMO reads them back. Screenshots are matched in order: one AMO already has gets its caption and position, and the missing ones are uploaded. AMO keeps its own copies, so a rerun cannot tell whether an image changed; a manual run can replace them all. AMO throttles writes (HTTP 429): the first runs were told to wait about a minute after three writes, then 40 minutes after about seven. So only fields that differ from what AMO returns are written, compared by their words because AMO rewrites links. A short throttle is waited out, and a long one stops the run with when to run it again; the next run resumes where it stopped.
- **The submission.** `scripts/publish_firefox_extension.py` replaces `scripts/sign_firefox_extension.py`:
  1. If AMO already holds this version as listed, it reports the file's status and stops.
  2. If AMO holds this version in another channel, the job fails, because a version number is taken once.
  3. Without a listing, it warns with how to make one and stops.
  4. Otherwise it uploads to the listed channel, waits for validation and creates the version. The license and listing carry over. A version AMO disables at once fails the job.
- **No waiting for the signature.** Mozilla signs and serves the approved file. The `firefox-extension` job no longer signs, and every release carries only `imprint-firefox-extension-unsigned.xpi`.

## Rejected alternatives

- **Keeping unlisted signing (ADR 0193).** It has no automatic updates, and the owner chose a listing.
- **Unlisted signing with a hosted update manifest (`update_url`).** ADR 0193 named this as the other route to updates. It was not put to the owner, who asked for store approval as on Chrome. It would also mean publishing an `updates.json` with every release.
- **Submitting the first listed version from the script, with a license.** The owner was already making it in the Developer Hub, and it is a single step, like the Chrome item.
- **Keeping the listing's content in the Developer Hub only.** The owner asked for the description and screenshots to be set as they were prepared for Chrome. Kept in the repository, they are reviewed like code and can be applied again.
- **Waiting for the listed version's signature and attaching it to the release.** Review time has no bound, and AMO already serves the signed file.

## Consequences

- Copies installed from the listing update once Mozilla approves each version.
- It was not checked whether Firefox moves copies installed from the v0.58.0 to v0.60.0 files to listed versions. Reinstalling from the listing is the sure path.
- Releases after v0.60.0 carry no signed add-on. Until the listing exists, the website keeps offering v0.60.0's signed file: it picks the newest stable release that has one.
- The first listed version must be a number AMO has not signed, and the safe choice is higher than 0.60.0 and lower than the next stable release. A prerelease build of a coming release must never be submitted by hand, because `v0.61.0-dev.1` is 0.61.0.1, which ranks above 0.61.0.
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
- `tests/test_update_amo_listing.py` ran eight tests, all passing, against a stand-in AMO. They covered the listing text and privacy policy, the screenshots with their positions and captions, categories in either shape, a partial upload completed without uploading again, a complete listing left alone, replacing every screenshot, throttled requests retried, a long throttle stopping the run, text AMO already holds (rendered, with rewritten links) left alone, and the listing files' summary length, category count and screenshot size.
- The first `amo-listing` runs on AMO (2026-11-22) set the listing text and categories and two screenshots. The first was throttled for 56 seconds while captioning the first screenshot. The second waited out a 58-second throttle, then was refused for 2,403 seconds. That led to the retry, the matching in order and writing only changed fields.
- addons-linter 10.16.0 reported no warnings on the add-on built from commit 4daa3bf.
- Not run: the real AMO listed submission, which waits for the listing and the next stable release.
