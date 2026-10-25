---
title: ADR 0031 — Website analytics events and public relay status
description: GA4 events for visitor actions on the website, a live public-relay status read from /health, and opt-in analytics on the relay's browser page.
---

- Status: Accepted
- Date: 2026-10-24
- Related: [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md), [ADR 0013](0013-remediate-security-scan-dependencies.md)
- Restructured: 2026-10-25

## Context

The website loaded Google Analytics for page views only. Downloads, copied install commands and the other actions on a page left no record, so there was no way to tell which install path visitors take. On the landing pages the GA bootstrap ran inside the function Astro's `define:vars` wraps around an inline script, so `gtag` was never global and no other script could have sent an event.

The public relay (`sync.alexandruleca.com`, also served as `imprint.alexandruleca.com`) gave visitors no sign that it is up or used. Its `/health` reported status, release and open rooms, but the website, a static GitHub Pages site on another origin, could not read it. The relay's browser page loaded no scripts at all, so visits to it were invisible too.

## Decision

- `site/public/scripts/events.js` is one capturing click and change listener, loaded with GA on landing and docs pages. It names events from the element: release-asset links are `download`, `role="tab"` controls `select_tab`, opened `<details>` `expand`, `<select>` changes `select_option`, other links `link_click` and other buttons `button_click`. Elements can declare their own event with `data-track` and `data-track-*`; the copy buttons use this for `copy_command`. Each event carries `placement`, taken from the nearest section id or landmark. The landing-page bootstrap now sets `window.gtag`.
- The relay counts rooms both peers joined (`sessions`) and reports them with its start time (`since`) on `/health`, which now sends `Access-Control-Allow-Origin: *`. The counters live in memory, so the relay stays stateless and a restart resets them. The landing page's Sync section reads `/health` in the browser and refreshes it every 30 seconds while visible. It shows the status, sessions since restart, open rooms and release, and links to the relay in a new tab.
- `imprint relay --ga4-id G-…` adds Google Analytics to the relay's browser page. The ID must match `^G-[A-Z0-9]+$`, and the snippet is added once, before `</head>`. Views are reported as `/` or `/not-found`, never the address, because a path can hold a share code. Without the flag the page is unchanged and loads no scripts. The ID is runtime configuration, not part of the image, so self-hosted relays never report to this project's property.

## Rejected alternatives

- **Annotate every link and button.** That is explicit, but docs pages render Starlight's markup, which can't be annotated, and new links would go untracked by default. Inferring from element type, with an override attribute, covers both.
- **GA's enhanced measurement only.** It records outbound clicks and downloads of some extensions (`.zip`, `.exe`, `.gz`, `.pkg`, but not `.dmg` or `.msi`). It misses tabs, copies and internal navigation, and can't say which button was used. Events use their own names (`download`, `link_click`) rather than `file_download` and `click`, so they never double-count with it.
- **A persisted, lifetime session count.** It would need a volume and a writable state file, and rolling updates run two relays at once. It would also break the relay's documented promise to keep nothing. A count since restart, labelled that way, is honest without state.
- **Exclude paired-device reconnects from `sessions`.** That would tie the relay to client room naming (`paired_…`). The relay counts what it forwards; the docs say reconnects count.
- **Bake the GA ID into the relay image or page.** Every self-hosted relay would then report to this project's property.
- **A dedicated stats endpoint.** `/health` is already public and polled; one endpoint keeps deploy checks and the website on the same data.

## Consequences

- Visitor actions arrive in GA4 under seven event names. `placement`, `command`, `tab_group`, `tab`, `label`, `option` and `version` need event-scoped custom dimensions in GA before standard reports show them. They are visible in DebugView and explorations without that.
- The website's relay status shows "couldn't reach" until a relay with the CORS header is deployed (the next stable release). With paired devices reconnecting every minute, `sessions` grows with automatic sync, not only with new pairings.
- The public relay's stack has passed `--ga4-id` since 2026-10-24. v0.18.0 ignores unknown flags, so the flag takes effect when a release containing this change is deployed, and the release webhook keeps the service's arguments. Visits to the relay page then appear in the same GA property under the `sync.alexandruleca.com` host.
- The relay page loads a third-party script only when its operator chooses to.

## Invalidation conditions

- If the relay's `/health` must stop being public, the website's relay status and the wildcard CORS header must be revisited.
- If the website adopts consent-gated analytics or leaves GA4, the always-on click listener must follow.

## Validation

- `go test ./cmd/ -run Relay` passes. The new tests check that a room counts once when both peers join and not while waiting, that `/health` returns `sessions`, `since` and the CORS header, that the page is unchanged without an ID, and that with one the analytics are added once in the head with the address replaced. Malformed IDs, including script injection, are refused. `go vet ./cmd/` is clean.
- A locally built relay with `--ga4-id G-ZPW1M6BCC7` served the snippet, answered `/health` with the CORS header and new fields, returned 404 for a share-code path, and exited 1 for `--ga4-id bad`.
- In Chromium against the Astro dev server, with Google's script blocked and `/health` mocked or proxied to that relay, the expected `dataLayer` events and parameters appeared. Covered: tab switches and copies in the hero and install tabs, a dashboard tab, nav, footer and relay links, a release-asset download, the dev-prerelease toggle, release-notes and FAQ disclosure (closing sends nothing), the Chrome page copy, and the docs theme select, Download and sidebar links. The relay status rendered online, from the real relay through CORS, and unreachable, with no horizontal overflow at 360, 390, 768 and 1440 px.
- The public relay was redeployed with `--ga4-id G-ZPW1M6BCC7` in its stack. The rolling update completed, and `/health` answered on both hostnames, still running v0.18.0.
- Not verified: events arriving in the production GA property, and analytics on the deployed relay page. Both need the release.
