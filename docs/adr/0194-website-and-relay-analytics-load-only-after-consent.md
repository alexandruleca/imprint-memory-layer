---
title: ADR 0194 — Website and relay analytics load only after consent
description: Google Analytics on the website and the relay's page loads only after the visitor accepts a banner; no answer, declining or Global Privacy Control loads nothing, and withdrawing stops it and deletes its cookies.
---

- Status: Accepted
- Date: 2026-11-20
- Amends: [ADR 0031](0031-website-analytics-and-public-relay-status.md) — the website's and the relay page's Google Analytics load only after the visitor consents
- Related: [privacy policy](../../site/src/pages/privacy.astro)

## Context

Since ADR 0031 every page of the website, and the relay's browser page when its operator passes `--ga4-id`, loaded `gtag.js` on arrival. Google Analytics then set `_ga` and `_ga_*` cookies on `.alexandruleca.com` and reported the visit, with no question asked. The public relay runs with the website's measurement ID (`G-ZPW1M6BCC7`), so both share one property and one cookie domain.

The privacy policy written on 2026-11-20 for the capture extension's Chrome Web Store listing had to describe this. Analytics cookies are not needed for either page to work, and cookie rules in the EU and the UK generally require asking before setting them. The owner asked for a banner that works correctly when a visitor declines.

ADR 0031 anticipated this: *If the website adopts consent-gated analytics or leaves GA4, the always-on click listener must follow.*

## Decision

**Nothing from Google Analytics loads before the visitor accepts.** One script, `site/public/scripts/consent.js`, decides. The landing layout and the Starlight head load it in builds with `PUBLIC_GA4_ID`. The relay inlines a byte-identical copy (`cmd/relay-consent.js`) before `</body>` when started with `--ga4-id`, and a Go test fails if the copies differ.

- **No answer, declining, or a browser sending Global Privacy Control:** no `gtag.js`, no `gtag` function, no cookie and no request. Any `_ga`, `_ga_*`, `_gid` or `_gat*` cookie left from before is expired on the host and each parent domain. Global Privacy Control counts as declining, so those visitors are not asked.
- **Accepting** loads `gtag.js` with analytics storage granted, the three advertising consent types denied, and Google signals and ad personalization off. The relay keeps reporting every view as `/` or `/not-found` (`data-page-location="root"`).
- **Withdrawing.** Any `[data-consent-open]` control reopens the banner with the current choice stated: **Cookie settings** in the landing footer and the relay footer, and a button in the privacy policy. Declining there sets `ga-disable-<id>`, updates consent to denied and deletes the cookies on the open page.
- **The choice** is kept per origin in `localStorage` (`imprint-consent`: version, choice, time) for 365 days, then asked again. Raising the script's version asks everyone again. When storage is unavailable, the choice holds for the page and the next page asks again.
- **The banner** is not modal and is first in the document. Accept and Decline are identical buttons. It follows the page's theme: Starlight's `data-theme="dark"`, or the system setting on a page without a theme of its own, such as the relay. Its controls carry `data-track-ignore`, which `events.js` now honours, so choosing is not reported as a visitor action. `events.js` itself stays loaded and sends nothing until `gtag` exists.
- `imprint relay --privacy-url https://…` gives the relay's banner a policy to link. Without it, the banner links nothing: a self-hosted relay is not covered by this project's policy.

## Rejected alternatives

- **Consent Mode "advanced" (load `gtag.js` for everyone with analytics storage denied by default).** Before any answer, and after a refusal, Google then receives cookieless pings carrying the visitor's IP address and browser. That does not meet the request that declining sends nothing.
- **Remembering the choice in a first-party cookie, or a cookie fallback when `localStorage` is blocked.** The cookie would travel with every request to GitHub Pages and the relay, and a fallback doubles the storage paths. A visitor who blocks storage is asked on each page instead.
- **Loading the website's `consent.js` into the relay page from the website.** Every self-hosted relay with analytics would then depend on this project's website and request a script from it. The relay embeds the file, and a test keeps the copies identical.
- **Showing the banner to Global Privacy Control browsers.** The signal already answers the question; asking again would be a request to override it.
- **An emphasized Accept button.** Declining must be as easy as accepting, so the buttons are identical.

## Consequences

- GA4 counts only visitors who accept. Visits, downloads and events fall from 2026-11-20, so trends across that date are not comparable.
- The website and the relay page are separate origins and ask separately. Cookies on `.alexandruleca.com` are deleted from either one.
- Starlight's docs pages show the banner but have no footer link of their own. The privacy policy's button and the landing footer reopen it.
- The relay change reaches the public relay with its next deploy. The stack's arguments need `--privacy-url https://imprintmcp.alexandruleca.com/privacy/#relay` for the banner to link the policy.
- `consent.js` changes must be copied to `cmd/relay-consent.js`, or the Go test fails.

## Invalidation conditions

- If the website moves to analytics that set no cookies and send nothing to a third party, or drops Google Analytics, remove the banner rather than ask a question that no longer applies.
- If Google Analytics stops honouring `ga-disable-<id>` or a consent update on a page where it has loaded, withdrawing must reload the page instead.
- If the relay page and the website need different banner texts or policies, the byte-identical copy must become a parameterized template.
- If what Google Analytics collects changes materially, raise the script's version so every visitor is asked again.

## Validation

- `go test ./cmd/ -run Relay` (Go 1.27.4, Linux) passes, with `go vet ./cmd/` and `gofmt` clean. New checks: the page is unchanged without an ID; with one it carries the banner once before `</body>` and Cookie settings once in the footer, and loads no Google script itself; the embedded file equals the website's; the privacy address is escaped into its attribute; non-https, credentialed and relative privacy addresses are refused.
- In Chromium (Playwright) against a static build with `PUBLIC_GA4_ID=G-TEST123`, with Google's endpoints stubbed so nothing reached Google, 47 checks passed. They cover a first visit (banner shown, first in the body, equal buttons, no request, no `gtag` or `dataLayer`) and declining (stored, not asked on landing, FAQ, privacy or docs pages, no request, no cookie). They cover accepting: stale `_ga` cookies expired before an answer, `gtag.js` loaded once, the config and consent states, the consent click not reported, a visitor link reported, and a reload not asking again. Withdrawing from the footer set `ga-disable`, deleted cookies, moved focus in and back, and a reload loaded nothing. Accepting again from the privacy page loaded `gtag.js`. Global Privacy Control showed no banner and sent nothing. Blocked storage asked on each page and threw no error. The banner went dark in Starlight's dark theme, and a 360 px phone had 16 px gutters with no horizontal scroll.
- A locally built relay with `--ga4-id G-TEST123 --privacy-url https://imprintmcp.alexandruleca.com/privacy/#relay` showed the banner on a share-code path, sent nothing before an answer, linked the policy, reported the accepted view as `/not-found` with the path in nothing sent, and stopped on withdrawal. In system dark mode its banner was dark, while the website's landing pages, which pin light, kept a light one. `--privacy-url http://bad` was refused.
- Not verified: hits in the production GA property after consent, and the deployed relay page. Both need the release and a relay deploy.
