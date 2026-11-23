---
title: ADR 0207 — The Firefox add-on is listed on addons.mozilla.org again
description: The owner submitted the first listed version by hand, so stable releases submit theirs to the add-on's addons.mozilla.org listing again and carry only the unsigned add-on; the website's Firefox button opens the listing once Mozilla approves it, and a long AMO throttle warns instead of failing the release.
---

- Status: Accepted
- Date: 2026-11-23
- Supersedes: [ADR 0206](0206-the-firefox-add-on-goes-back-to-unlisted-signing.md)
- Amends: [ADR 0193](0193-firefox-capture-is-the-chrome-extension-signed-as-an-unlisted-add-on.md) — stable releases submit a listed version to addons.mozilla.org instead of signing an unlisted one, and carry only the unsigned add-on
- Related: [ADR 0203](0203-the-firefox-add-on-is-listed-on-addons-mozilla-org.md) (the first listed route, whose submission this restores), [ADR 0202](0202-stable-releases-submit-the-extension-to-the-chrome-web-store.md) (the Chrome Web Store, unchanged), [browser extension](../browser-extension.md#store-publishing)

## Context

- **Unlisted signing was throttled too.** Under ADR 0206, v0.62.0 signed at 07:14 UTC on 2026-11-23. AMO accepted the upload but refused to create the version "for 225 more minutes". v0.62.1, about two hours later, was refused "for 108 more minutes". Both releases shipped only `imprint-firefox-extension-unsigned.xpi`, as ADR 0206 intended, and the website kept offering v0.60.0's signed file. AMO never created 0.62.0 or 0.62.1.
- **The owner made the listing.** The owner asked whether the add-on could be published on AMO once the throttle lifted. Later that day they uploaded version 0.60.0.1 in the Developer Hub with **Upload New Version → On this site**. That was the manual first step ADR 0203 needed. The Developer Hub then showed the add-on with the listed version 0.60.0.1 **Awaiting Review**, beside the unlisted ("Self") versions. AMO's public API still answered HTTP 401 for the add-on at 13:12 UTC, so no public listing page exists until Mozilla approves a listed version.
- **The listing's content is on AMO.** The `amo-listing` runs of 2026-11-22 set the summary, description, categories and screenshots (ADR 0203). They stayed in the add-on's record after ADR 0206 removed the tooling.
- **One channel per number.** AMO takes each version number once, across the listed and unlisted channels. A release can be signed unlisted for the website or submitted to the listing, not both.
- **The website follows a successful Release.** `pages.yml` rebuilds the site after each Release run on `main`, but only when that run succeeds. A failed job after `publish` fails the run, so the download page would miss that release.

## Decision

- **Submit to the listing.** The `firefox-listing` job comes back after `publish`, for stable releases only, with the existing `AMO_JWT_ISSUER` and `AMO_JWT_SECRET` secrets. Without them it warns and submits nothing. `scripts/sign_firefox_extension.py` becomes `scripts/publish_firefox_extension.py` again, with ADR 0203's steps:
  1. If AMO holds the release's number as listed, it reports the file's status and stops.
  2. If AMO holds the number in another channel, the job fails.
  3. If the add-on has no listing, it warns with how to make one and stops.
  4. Otherwise it uploads to the listed channel, waits for validation and creates the version. The license and listing carry over. A validation failure or a file AMO disables at once fails the job.
- **Releases carry only the unsigned add-on.** The `firefox-extension` job no longer signs. Mozilla signs the listed version it approves and serves it from the listing.
- **A long throttle warns.** The client keeps ADR 0206's retry: it waits out an HTTP 429 that ends within 20 minutes. When AMO asks for a longer wait, the script prints a warning naming the wait and the number, and the job succeeds without submitting. Rerunning the job after the wait submits that number; otherwise the next stable release submits its own.
- **The website opens the listing once it is public.** When the site is built, `site/scripts/build-releases.mjs` reads AMO's public detail of `capture@imprintmcp.alexandruleca.com`. If it is `public` with a current version and a page on addons.mozilla.org, the extension page's button reads **Add to Firefox** and opens that page, and the FAQ says Firefox updates the add-on. Otherwise, including any failed lookup, the button stays **Firefox add-on** and downloads the newest stable release's signed file, which is v0.60.0's.
- **The listing's content stays in the Developer Hub.** The `amo-listing` workflow, `scripts/update_amo_listing.py` and `extensions/store/` are not restored.

## Rejected alternatives

- **Staying on unlisted signing (ADR 0206).** The add-on would never update itself. Unlisted signing is throttled the same way: two releases in a row were left unsigned on the day this was decided. And the owner has now made the listing.
- **Signing unlisted and submitting the same release to the listing.** AMO takes each number once, so the second request would be refused.
- **Failing the job on a long throttle, as ADR 0203 did.** The failure would fail the Release run, and `pages.yml` would then skip the website's rebuild for a release whose packages all shipped.
- **Pointing the website at the listing straight away.** The listing has no public page until Mozilla approves a listed version (AMO answers 401). Until then the button would lead nowhere, while v0.60.0's signed file still installs.
- **Restoring the listing tooling.** AMO already holds the text and screenshots those files described, and nothing needs changing. AMO throttles listing writes hard: the 2026-11-22 runs were refused for 2,403 seconds after about seven writes. The tooling remains in history at commit 301459a if the listing needs to be rewritten from the repository.
- **Unlisted signing with a hosted update manifest (`update_url`).** As in ADRs 0203 and 0206, it would need an `updates.json` published and served with every release. The owner chose the listing.

## Consequences

- Copies installed from the listing update once Mozilla approves each version. It was not checked whether Firefox moves copies installed from the self-hosted files (0.58.0 to 0.60.0) to listed versions; reinstalling from the listing is the sure path.
- Until Mozilla approves 0.60.0.1, the website offers v0.60.0's signed file. It switches to the listing at its first rebuild after the approval: the next successful stable Release, or a manual run of `pages.yml`.
- Releases after v0.60.0 carry no signed add-on. Mozilla serves the signed file of each approved listed version.
- The first version CI submits will be the next stable release's (0.62.2 or higher). AMO does not hold 0.62.0 or 0.62.1.
- A release whose submission was throttled is missing from the listing unless its job is rerun. The next stable release's version is higher, so it replaces it.
- A prerelease build must still never be uploaded by hand: `v0.62.2-dev.1` becomes 0.62.2.1, which ranks above 0.62.2.

## Invalidation conditions

- If Mozilla rejects or removes the listing, return to unlisted signing (ADR 0206; the signer is at the commit before this change) or host an update manifest, in a new record.
- If AMO refuses a listed version while an earlier one awaits review, make that answer a warning like the long throttle, so that it does not fail the Release run.
- If throttling leaves stable releases off the listing more than occasionally, submit from a separate scheduled or rerunnable workflow.
- If AMO changes or retires its v5 API, update the script or move to Mozilla's supported tool.

## Validation

- Linux x64 (WSL2), Python 3.14: `tests/test_publish_firefox_extension.py` ran six tests against a stand-in AMO that checks every request's JWT, all passing with ResourceWarnings as errors. Four are restored from ADR 0203:
  - a new listed version, with its upload's channel;
  - a version already listed;
  - no listing, with no channels or only `unlisted`;
  - a validation failure, a disabled file, and a number held as unlisted.

  Two are new:
  - a throttle of a few seconds on version creation is waited out;
  - a throttle of 6,480 seconds ("108 more minutes", as v0.62.1 was told) raises `Throttled`, and `main` prints a warning naming the number.
- Node 22.23.5: `node --test site/scripts/*.test.mjs` ran ten tests, all passing. The new ones cover reading AMO's detail (public, awaiting review, a page elsewhere, nothing), choosing the listing or the signed file, and the site's add-on ID matching the packager's `GECKO_ID`.
- AMO's public detail of a listed add-on (uBlock Origin) has the `url`, `status` and `current_version.version` fields the site reads. For this add-on it answered 401 on 2026-11-23 at 13:12 UTC.
- `astro build`: with AMO's 401, the Firefox button linked v0.60.0's `imprint-firefox-extension.xpi`. With a stand-in listing in `releases.json`, the button read **Add to Firefox**, linked the listing, and the FAQ said Firefox updates it.
- The workflow's job list was read back from the YAML: `firefox-listing` follows `publish`, after `chrome-web-store`.
- Not run: a real submission from CI, which waits for the next stable release.
