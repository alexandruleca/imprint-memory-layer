---
title: ADR 0191 — Chrome full-page screenshots are stitched in the popup
description: Capture a whole page for OCR by scrolling it one viewport at a time and stitching the captures, without the debugger permission.
---

- Status: Accepted
- Date: 2026-11-19
- Amends: [ADR 0187](0187-browser-file-capture.md) — a screenshot can cover the whole page, not only the visible tab
- Related: [ADR 0190](0190-ocr-reads-tall-images-in-bands.md), [ADR 0185](0185-local-chrome-capture.md)

## Context

ADR 0187 offers OCR of the visible tab: `chrome.tabs.captureVisibleTab` returns
what the viewport shows and nothing below it. The user asked for a choice between
the current view and the full height of the page. Chrome gives extensions no
full-page capture outside the debugger protocol, and `captureVisibleTab` may be
called at most twice a second (`MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND`).

## Decision

The popup's **Screenshot text** row offers **Visible** (the ADR 0187 capture) and
**Full page**. Full page runs in the popup with the permissions the extension
already has (`activeTab`, `scripting`):

- A function injected into the tab records its scroll position and the document's
  height, then scrolls one viewport at a time with instant scrolling. The popup
  captures each viewport, at most one every 550 ms, and draws it on an
  `OffscreenCanvas` at the position the page actually scrolled to.
- Elements with `position: fixed` are hidden from the first viewport on, because they
  cover the text under them and repeat in every capture; `position: sticky` elements
  stay in place in the first viewport and are hidden from the second. Hiding sets
  `visibility: hidden !important` through the CSS object model, which page
  content-security policies do not block.
- The page is put back afterwards: scroll position and every changed style. The
  injected state keeps a 5 s watchdog that does the same if the popup closes in the
  middle of a capture.
- The canvas is bounded to 32,000 px tall and 48 million pixels; a longer page keeps
  its top part and the preview says so. The image is a PNG, re-encoded as JPEG at
  quality 0.85 when the PNG exceeds Imprint's upload limit; a capture still over the
  limit is refused with a pointer to Visible or Page text.
- A page whose document does not scroll (one that scrolls inside a panel, such as
  Gmail) yields its visible area, and the preview says why.

The result is reviewed and saved like any screenshot: `kind: screenshot`, file
`full-page.png`, read by the extractor in bands ([ADR 0190](0190-ocr-reads-tall-images-in-bands.md)).

## Rejected alternatives

- **The debugger protocol** (`Page.captureScreenshot` with `captureBeyondViewport`).
  One call would capture the page exactly, but it needs the `debugger` permission,
  whose install warning asks for far more than this extension uses, and Chrome shows
  a "started debugging this browser" bar during every capture.
- **Hiding elements with `chrome.scripting.insertCSS`.** Undoing it needs
  `removeCSS` from the popup; if the popup closed mid-capture the page would stay
  changed. Inline styles let the page's own watchdog restore it.
- **Capturing in the service worker.** It would outlive the popup, but the stitched
  image would have to cross extension messaging as base64 to be previewed. The page
  watchdog already covers a closed popup, and a closed popup has no preview to fill.
- **Scrolling inner panels.** Finding the element that scrolls a web app is guesswork
  per site; the visible area with a note is predictable.

## Consequences

- Capture time grows with the page: 14 viewports took 7.3 s. The page visibly scrolls
  while it runs.
- The height is measured once at the start, so content added while scrolling (infinite
  feeds) is cut at that height.
- Hidden fixed and sticky elements (navigation bars, cookie banners, chat buttons,
  sticky table headers below the first viewport) are missing from the image. Their
  text would otherwise repeat in every viewport.
- A capture can hold a canvas of up to 48 million pixels (about 190 MB) in the popup
  while it is encoded.
- No new permission: users updating the extension see no new install warning.

## Invalidation conditions

- If Chrome offers extensions a full-page capture without the `debugger` permission,
  use it and drop the stitching.
- If Chrome changes the capture quota, change the 550 ms interval with it.
- If capturing pages that scroll inside a panel becomes necessary (Gmail is the common
  case), decide how to find the panel in a new record.

## Validation

- The browser smoke test loads the packaged ZIP in a disposable Chromium and captures
  a 3,640 px fixture page with a sticky header and a fixed banner, scrolled to 500 px,
  with `captureVisibleTab` substituted by a viewport screenshot: the upload is a
  3,640 px tall `full-page.png` of kind `screenshot`, and the page is back at 500 px
  with both elements visible.
- A local harness ran the real `captureVisibleTab` on a 70-section article (a test-only
  copy of the extension with `<all_urls>`, because `activeTab` needs a toolbar click):
  a 1,280 × 10,843 PNG in 7.3 s, the sticky header once at the top, the fixed banner
  absent, the article restored; OCR read all 70 paragraphs and headings (ADR 0190).
- Not run: a toolbar click in headed Chrome on Windows or macOS, displays with a device
  pixel ratio above 1, and a page long enough to reach the bounds.
