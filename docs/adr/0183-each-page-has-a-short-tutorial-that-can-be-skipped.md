---
title: ADR 0183 — Each page has a short tutorial that can be skipped
description: A welcome on a new installation and a few steps per page, each a card beside the control it describes, start once per browser, can be skipped singly or all at once, and are replayed from the top bar's help menu.
---

- Status: Accepted
- Date: 2026-11-11
- Related: [ADR 0182](0182-the-dashboard-has-a-simple-and-an-advanced-form.md) (the two dashboards the steps are written for), [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) (one component kit), [dashboard](../dashboard.md#tutorials)

## Context

With the simple dashboard (ADR 0182) the owner asked for a tutorial for each functionality, which users can skip. The dashboard had no onboarding: an empty workspace showed three numbered steps on Home, and every page explained itself in a sentence under its title.

A tutorial has to work on pages whose content depends on data (a list may be empty, Echo may not be set up), in two dashboards that show different controls, in the desktop app and in a phone-width browser, and must not get in the way of someone who already knows the page.

## Decision

**Every page listed in `pages.ts` has one tutorial** of at most six steps (`imprint/ui/src/lib/tours.ts`), and a welcome precedes them on a new installation. A step has a title, one or two sentences and, usually, the `data-tour` name of a control. A step can belong to one dashboard only, so Simple and Advanced each read their own words, and can be marked optional when its control depends on data or on the desktop app.

**A step is a card beside its control,** which is outlined while the rest of the page dims; a step without a control sits in the middle. The page stays usable under it: the layer takes no pointer events and does not trap focus. The card is a dialog that is not modal, takes focus on its Next button and follows its control as the page scrolls or resizes. Under 640 px it is a sheet at the bottom.

**Starting.** With `ui.tutorials` on, the welcome starts first, then the open page's tutorial, each once per browser (`imprint.tours.seen` in `localStorage`). A tutorial waits up to eight seconds for its first control, starts only with the window visible, no dialog open and no recording running, and leaves out the steps whose control is not on the page. The welcome offers the Simple or Advanced dashboard, which applies as it is chosen.

**Skipping.** Skip, the ✕ and Escape end a tutorial and mark it seen; nothing else starts by itself until another page opens. **Skip all tutorials** sets `ui.tutorials` off. Leaving the page ends a tutorial without marking it seen. While a dialog of the page is open the tutorial is hidden.

**Replaying.** The top bar's help menu shows the open page's tutorial or the welcome whether seen or not, switches `ui.tutorials`, and forgets what was seen.

**An installation already in use starts with `ui.tutorials` off** (written with its first `ui.mode`, ADR 0182).

## Rejected alternatives

- **A tour library** (driver.js, Shepherd, React Joyride). The dashboard has one component kit and its own colour tokens for both themes (ADR 0039, ADR 0154); a library brings its own markup, styles and focus handling to reconcile with them, and a dependency for about 300 lines. The component uses the kit's button and the theme's tokens.
- **A modal tutorial that blocks the page.** Someone who wants to try the control a step describes would have to leave the tutorial first, and a tutorial that started at the wrong moment would stand between the user and what they came to do.
- **Keeping what was seen in `config.json` or on the server.** It is not a setting: it changes on every first visit, and a second person's browser on the same device should be shown around too. The on/off switch is a setting, since it is a choice about the device.
- **Starting tutorials on installations already in use.** Their owners would be walked through every page they know, one visit at a time. They can turn tutorials on, and the help menu replays any of them.
- **Waiting for a step's control to appear** (asking the user to add something first). The tutorial would stall on a page without data. Such steps are optional and left out; the tutorial is short enough to replay later.

## Consequences

- A new page needs a tutorial and a page whose controls move needs its `data-tour` names kept: the tests fail otherwise.
- A rewritten tutorial is shown again only under a new id.
- The first step of every tutorial points at something the page always shows, so most first steps describe the page's header.
- The dim layer covers the page while a step shows, although the page is still usable under it; nothing says so.
- The steps are in English only, like the rest of the dashboard.

## Invalidation conditions

- If people report the dimmed page as blocked, drop the dim and keep the outline.
- If most new installations choose Skip all tutorials at the welcome, start only the welcome by itself and leave page tutorials to the help menu.
- If the dashboard is translated, move the steps' words to wherever its other words go.
- If a kit component for guided steps is adopted for the dashboard, replace this one with it.

## Validation

- Playwright, Chromium on Linux (WSL), against mocked API answers. `tours.spec.ts`: the welcome with its choice of dashboard, focus on Next, a step placed below its control inside the window, typing in the page under a step, Escape, Skip all and the help menu's replay and reset, nothing starting with tutorials off, a tutorial hidden behind a dialog and back, the sheet at 390 px. `page-tours.spec.ts`: one tutorial per page and none for a page that does not exist, at most six steps, a first step that is never optional, every step not marked optional visible on its page in both dashboards (36 page loads), a page's tutorial starting on the first visit and not the second, the advanced wording, leaving a page, and a step left out when its control is absent.
- Not run: a screen reader, the desktop app's windows, a touch device, and anyone following a tutorial for the first time.
