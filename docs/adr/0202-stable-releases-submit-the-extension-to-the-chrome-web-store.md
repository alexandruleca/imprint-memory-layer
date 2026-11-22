---
title: ADR 0202 — Stable releases submit the extension to the Chrome Web Store
description: After a stable release is published, CI uploads its extension to the existing Chrome Web Store item and submits it for review to go live once approved, so Chrome updates installed copies; the release ZIP stays for loading by hand.
---

- Status: Accepted
- Date: 2026-11-22
- Amends: [ADR 0030](0030-chrome-extension-release-zip.md) — the Chrome Web Store is the extension's install and update path; the release ZIP remains for loading by hand
- Related: [ADR 0193](0193-firefox-capture-is-the-chrome-extension-signed-as-an-unlisted-add-on.md) (one source, release versions), [ADR 0203](0203-the-firefox-add-on-is-listed-on-addons-mozilla-org.md) (the Firefox counterpart), [browser extension](../browser-extension.md#store-publishing)

## Context

- **The request.** On 2026-11-22 the owner asked for releases to push automatic updates to Chrome and request publication approval. They had created the item `ammleledmkghliflljeapomaaccnhmme` (Imprint Capture) in the Developer Dashboard by hand. It passed review and is live in the Chrome Web Store.
- **Before this record.** Chrome users loaded the release ZIP with Developer mode (ADR 0030). That copy never updates itself: each release meant replacing the folder and clicking Reload.
- **What the store's API allows.** Google's documentation was read on 2026-11-22:
  - The Chrome Web Store API v2 uploads a package to an existing item, publishes it, and reports the item's published and submitted revisions. The API cannot create items. v1 was supported only until 2026-10-15.
  - The API accepts a service account whose email is added under **Account** in the Developer Dashboard, one per publisher.
  - The package must have `manifest.json` at its root, and each upload needs a higher version than the last.
  - Review usually finishes "within a few days, but it can take up to a few weeks".
- **Release cadence.** Stable releases v0.58.0 and v0.59.0 came two days apart (2026-11-20 and 2026-11-22). That is faster than a review can be counted on to finish.
- **The owner's choices** (asked 2026-11-22): a version goes live as soon as it is approved, and CI signs in with a service account JSON key.

## Decision

- **The store's package.** `scripts/package_chrome_extension.py --browser chrome-web-store` writes `imprint-chrome-web-store.zip`. It holds the release ZIP's files, byte for byte, at the archive's root and without `INSTALL.txt`. The `chrome-extension` job builds it after the browser smoke test and keeps it as a workflow artifact, not a release asset.
- **When.** A new `chrome-web-store` job runs after `publish`, for stable releases only, so the store never receives a version whose GitHub release does not exist. Prereleases are never uploaded.
- **Credentials.** The repository secrets are `CWS_SERVICE_ACCOUNT_JSON` (the key file's JSON) and `CWS_PUBLISHER_ID`. Without them the job warns and submits nothing.
- **The submission.** `scripts/publish_chrome_extension.py` uses the standard library. The `openssl` command signs the service account's RS256 token, which Google exchanges for an access token with the `chromewebstore` scope. Then the script:
  1. reads the item's status;
  2. stops if the store already publishes this version, or has it submitted (a rejected or cancelled submission fails the job);
  3. stops with a warning if an earlier version is still `PENDING_REVIEW`, so that review can finish; rerunning the job afterwards, or the next release, submits;
  4. otherwise uploads the package, waits while the store processes it, and publishes with `DEFAULT_PUBLISH`, printing the store's warnings.
- **Links.** The item ID is fixed in the script. The website and docs link to the listing.

## Rejected alternatives

- **`STAGED_PUBLISH`**, with each approved version waiting for the owner to publish. The owner chose to go live on approval.
- **An OAuth client and refresh token**, as in the v1 guides. The token belongs to a person. It also expires after seven days unless the OAuth consent screen is in production. A service account avoids both.
- **Keyless Workload Identity Federation** from GitHub's OIDC token. It stores no key, but needs more Google Cloud setup. The owner chose the JSON key; federation is the fallback if the organization forbids key creation.
- **Cancelling a pending review to submit the newer version.** Releases come faster than reviews are guaranteed to finish, so cancelling on every release could keep any version from ever passing.
- **Submitting from the `chrome-extension` job**, before `publish`. A later job's failure would then leave a store version without its release.
- **The `cryptography` package or Google's auth library** for the RS256 signature. The store jobs use a bare `actions/setup-python` interpreter. Installing hashed dependencies for one signature costs more than calling `openssl`.
- **RSA signing in pure Python.** That means hand-written cryptography over a long-lived key.

## Consequences

- Copies installed from the store update by themselves once a version passes review. Copies loaded from the ZIP still update by hand.
- The store lags each release by its review. Releases published during a review are not submitted until the job is rerun or the next release comes.
- Only three-part stable versions reach the store. A prerelease's fourth number (`v0.60.0-dev.1` is 0.60.0.1) would rank above the next stable release and block it.
- The store listing, privacy answers and visibility stay in the Developer Dashboard. The API publishes with the item's current visibility.
- The JSON key is a long-lived credential in the repository's secrets. Rotate it in Google Cloud and update the secret.

## Invalidation conditions

- If Google changes or retires the v2 API, or the service account route, update the script or move to the supported route.
- If the organization forbids service account keys, or the key is withdrawn, move to Workload Identity Federation.
- If the `openssl` command is missing on the runners, install `cryptography` from the hashed lock file for the signature.
- If the store gains a way to replace a pending submission without resetting its review, or reviews reliably finish within a release cycle, revisit the rule that waits for an earlier review.
- If the extension leaves the Chrome Web Store, return to the ZIP alone (ADR 0030).

## Validation

- Linux x64 (WSL2), Python 3.14, OpenSSL 3.5.5: `tests/test_publish_chrome_extension.py` ran six tests, all passing, against a stand-in for Google's token endpoint and the v2 API. They covered:
  - a new version uploaded and published with `DEFAULT_PUBLISH`;
  - an upload the store processes asynchronously;
  - versions already published or submitted;
  - an earlier version still in review;
  - a failed upload, a failed asynchronous upload and a rejected submission;
  - an RS256 token from a fresh 2048-bit key, which `openssl dgst -verify` accepted with the public key.
- `tests/test_chrome_extension_package.py` checks that the store package is the release ZIP's files at the root, without `INSTALL.txt`.
- Not run: the real Chrome Web Store API, and the job on the self-hosted runners. Both wait for the secrets and the next stable release.
