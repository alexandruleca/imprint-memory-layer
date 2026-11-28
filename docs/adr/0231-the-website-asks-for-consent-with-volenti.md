---
title: ADR 0231 — The website asks for consent with Volenti
description: The website's own consent banner is replaced by Volenti; Google Analytics stays inert until the visitor allows Statistics, and the relay page keeps its own banner.
---

- Status: Accepted
- Date: 2026-11-28
- Amends: [ADR 0194](0194-website-and-relay-analytics-load-only-after-consent.md) — the website asks with Volenti instead of `consent.js`; the relay page keeps that banner
- Related: [ADR 0031](0031-website-analytics-and-public-relay-status.md), [privacy policy](../../site/src/pages/privacy.astro), [site README](../../site/README.md#analytics)

## Context

Since ADR 0194 the website asked before loading Google Analytics with its own script, `site/public/scripts/consent.js`, which kept the visitor's choice only in their browser's `localStorage`. The owner asked on 2026-11-28 to replace it with Volenti, the consent service they are building, and gave its install snippet for the site `avwdzx6wliubqo2bazgk`. Volenti adds what `consent.js` did not have: a record of each decision on its server (a random consent ID, the categories, the action and the texts' version), a panel with one switch per category, and texts and colours managed outside the repository.

The site's published Volenti settings, read from its loader and UI config on 2026-11-28, are: domain `imprintmcp.alexandruleca.com`, consent mode `basic`, a choice lifetime of 182 days, categories Preferences, Statistics and Marketing, and no privacy policy address. Volenti's loader (`sdk/src/loader.ts` in the Volenti repository) runs only on the site's domain and subdomains, its staging hostname, and `localhost`, `*.test` and `*.local`. It defines `gtag()` and sets every Google consent signal to denied. It runs scripts tagged `type="text/plain" data-volenti-category="…"` in document order once their category is allowed, and deletes the cookies their `data-volenti-cookies` name when the category is withdrawn. It treats Global Privacy Control as a refusal and records it as `signal`.

## Decision

**In builds with `PUBLIC_GA4_ID`, every page of the website installs Volenti's snippet first in `<head>`, and Google Analytics runs only once the visitor allows Statistics.** One module, `site/src/lib/volenti.mjs`, holds the tags, and both the landing layout (`Analytics.astro`) and the Starlight head render them.

- **Google Analytics is tagged for Volenti's Basic mode.** `gtag.js` carries `data-volenti-cookies="_ga _ga_* _gid _gat*"`. It is followed by `public/scripts/analytics.js`, which sends the `config` with Google signals and ad personalization off. Both are `type="text/plain" data-volenti-category="statistics"`. No answer, rejecting or Global Privacy Control loads nothing from Google, as under ADR 0194.
- **Withdrawing on an open page** sets `ga-disable-<id>`, from a `volenti:change` listener in `analytics.js`. Volenti itself updates consent to denied and deletes the cookies.
- **`events.js` sends an event only while Volenti reports Statistics allowed.** The snippet defines `gtag` for every visitor, so the old test (does `gtag` exist?) no longer means consent. It also ignores clicks on `[data-volenti-open]` controls, which open the cookie settings rather than being visitor actions.
- **Cookie settings** is the footer link `<a href="#cookie-settings" data-volenti-open>` and the privacy policy's button, both `data-volenti-open`.
- **`site/public/scripts/consent.js` is removed.** The relay page keeps its banner, now only as `cmd/relay-consent.js`, and ADR 0194 still governs it. The Go test that compared the two copies is gone.
- The privacy policy describes Volenti: the `volenti_consent` cookie, the consent record sent to `gdpr.sys.cloud.alexandruleca.com`, and Volenti as a recipient.

## Rejected alternatives

- **Volenti on the relay page too.** The Imprint site's Volenti domain is `imprintmcp.alexandruleca.com`, so its loader refuses to run on `sync.alexandruleca.com`. A self-hosted relay is not this project's site either. ADR 0194 rejected making relays depend on the website for their banner, and the same reasoning applies to depending on a consent service.
- **Consent Mode "advanced" (load `gtag.js` for everyone with consent denied).** ADR 0194 rejected it because Google then receives cookieless pings after a refusal. The Volenti site is configured `basic`, and its loader warns when a Google tag is left untagged in that mode.
- **Leaving `events.js` to test whether `gtag` exists.** The snippet defines `gtag` for everyone. Events from visitors who had not answered would then queue in `dataLayer`, and `gtag.js` would read them if the visitor accepted later on the same page.
- **Keeping `consent.js` next to Volenti, or migrating its stored choices.** Two banners would ask the same question, and Volenti's records start from a decision made in its own banner. Every visitor is asked once more, and the old `imprint-consent` entry stays unused in their `localStorage`.

## Consequences

- Every visitor who answered the old banner is asked again. GA4 counts change from the release that ships this, as they did on 2026-11-20.
- The choice now lives in a first-party cookie, `volenti_consent`, sent with each request to GitHub Pages, and lasts 182 days instead of 365. ADR 0194 had kept it out of cookies, but Volenti must read the decision synchronously before any tag runs.
- Each decision sends a consent record to Volenti's server. The banner and its texts load from that server, so a visitor's first page also requests Volenti's UI build and config.
- The banner's texts, colours and categories are managed in Volenti, not in this repository. On 2026-11-28 the banner mentioned "statistics and marketing" although only Statistics is used here, and it linked no privacy policy.
- Volenti deletes the `_ga` cookies only when a category the visitor had allowed in Volenti is withdrawn. Cookies left from the old banner stay until they expire or the visitor withdraws, but no Google script loads to read them while Statistics is not allowed.
- Starlight's docs pages still have no Cookie settings link of their own, as under ADR 0194.

## Invalidation conditions

- If the website drops Google Analytics, or moves to analytics that set no cookies and send nothing to a third party, remove the snippet rather than ask a question that no longer applies (as in ADR 0194).
- If Volenti's site for the website changes to consent mode `advanced`, or the snippet stops setting every signal to denied by default, revisit this record. Declining must still send nothing.
- If Volenti stops treating Global Privacy Control as a refusal or stops running `type="text/plain"` tags only after consent, Google Analytics would load without consent: return to a banner of the website's own.
- If the relay moves under a Volenti site that covers its hostname and self-hosted relays no longer need a banner of their own, the relay can move to Volenti and `cmd/relay-consent.js` can go.

## Validation

- `PUBLIC_GA4_ID=G-TEST123 npx astro build` (Node 22.23.5, Astro 7.4.13) built 275 pages. A script over the built HTML checked all 277 files. On every page the snippet is the first script in `<head>`. The loader appears once. `gtag.js` and `analytics.js` are present only as `type="text/plain"`, and nothing references `consent.js` or `data-consent-open`. The one exception is ADR 0194's own page, which names the old script in its text.
- In Chromium (Playwright, WSL), with the built site served on `localhost`, the real Volenti loader and UI ran 39 checks and all passed. Volenti's consent endpoint and Google's endpoints were stubbed, so no record reached the site's ledger and nothing reached Google.
  - **First visit:** the banner shows, consent defaults to denied, both Google tags stay inert, and no Google request is made. A visitor action queues no event.
  - **Reject all:** sends a `reject_all` record with choices 0 and sets `volenti_consent`. The landing, FAQ and docs pages then do not ask again, and Google is never requested.
  - **Accept all:** loads `gtag.js` once for the ID. The consent update to granted precedes the `config`, and Google signals and ad personalization are off. A tab switch is reported as `select_tab`.
  - **Cookie settings:** the footer link opens the panel without changing the address and is not reported.
  - **Withdrawing Statistics:** recorded as `withdraw`. It sets `ga-disable-G-TEST123`, deletes a planted `_ga` cookie and updates consent to denied. Later actions are not reported, and a reload loads nothing and does not ask.
  - **Privacy policy:** its button reopens the panel, and allowing Statistics again loads `gtag.js`.
  - **Docs pages** show the banner and load `gtag.js` only after Accept all.
  - **Global Privacy Control:** no banner, a `signal` record with choices 0, and nothing from Google.
  - **Phone:** at 360 px the banner causes no horizontal scroll.
- `go test ./cmd/ -run Relay`, `go vet ./cmd/` and `gofmt` (Go 1.27.4, Linux) pass after `TestRelayConsentMatchesSite` was removed.
- Not verified: the production site on `imprintmcp.alexandruleca.com`, real consent records in Volenti's ledger, and hits in the production GA4 property. These need the release.
