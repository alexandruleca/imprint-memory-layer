---
title: ADR 0190 — OCR reads tall images in bands
description: Read an image taller than RapidOCR's 2,000 px limit in overlapping bands, keeping each line once, instead of letting the engine shrink the whole image.
---

- Status: Accepted
- Date: 2026-11-19
- Related: [ADR 0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md), [ADR 0191](0191-chrome-full-page-screenshots-are-stitched-in-the-popup.md)

## Context

The default OCR backend, RapidOCR 3.10.0 ([ADR 0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md)),
resizes every input whose longer side exceeds `Global.max_side_len` (2,000 px in
its bundled `config.yaml`) down to that size before detection
(`resize_image_within_bounds` → `reduce_max_side`). `imprint/extractors/ocr.py`
passed whole images to it. A tall image is shrunk by its height, so a page
screenshot 10,000 px tall is read at a fifth of its size and its text becomes too
small to detect.

Measured on the Windows install's runtime (RapidOCR 3.10.0, CPU):

- A synthetic 1,280 × 12,000 page with 333 lines of 18 px Arial: one pass read 0 lines.
- A real Chromium full-page screenshot of a 70-section article, 1,280 × 10,843: one
  pass read 0 of 70 paragraphs.

Full-page screenshots from Chrome ([ADR 0191](0191-chrome-full-page-screenshots-are-stitched-in-the-popup.md))
are routinely this tall, and long uploaded images (receipts, exported slides,
mobile screenshots) hit the same limit.

## Decision

`recognize` reads an image no taller than 2,000 px with one engine call, exactly
as before. A taller image is cut into horizontal bands 2,000 px tall that overlap
by at least 200 px; the last band ends at the bottom edge. Each pair of
neighbouring bands splits its overlap in the middle, and a recognised line is kept
only from the band whose share contains the vertical centre of its box. A line up to
200 px tall therefore lies whole inside the band that keeps it, and no line is kept
twice. Lines are joined band by band, in the engine's reading order within each band.

The width is not banded: an image wider than 2,000 px is still shrunk by its width,
as before. The Tesseract path for other `ingest.ocr_lang` values has no such limit
and is unchanged.

## Rejected alternatives

- **Raise `max_side_len` for tall images.** Detection would then run over the whole
  page at once, with memory and time growing with its height. Not measured; banding
  keeps every engine call the size the bundled models are configured for.
- **Bands without overlap.** A line that crosses a seam would be cut in two and
  misread in both bands. The overlap with centre ownership avoids that without
  deduplicating text by content.
- **Splitting the screenshot in the extension and uploading each slice.** One capture
  would become many queue jobs and documents, and other tall images (uploads, PDF
  pages) would still shrink. Banding in the extractor serves every caller.

## Consequences

- Text in tall images is readable: the measurements above went from 0/333 lines to
  333/333 and from 0/70 paragraphs to 70/70, with no line repeated.
- Time grows with the number of bands: 12.1 s for the 12,000 px page (6.9 s for the
  single pass that read nothing) and 17.4 s for the 10,843 px article.
- PDF pages render at scale 2 before OCR, so Letter (1,224 × 1,584) and A4
  (1,190 × 1,684) pages stay under the limit and keep one pass; only longer pages are
  banded.
- The extraction profile is unchanged, so a tall image indexed before this change
  keeps its old text until it is ingested again.
- RapidOCR's text-direction classifier misread repeated short synthetic lines as
  upside down (18 of 55 exact with it, 55 of 55 without) but garbled nothing on the
  real article page. It stays on; see the invalidation conditions.

## Invalidation conditions

- If RapidOCR raises its default `max_side_len` above the images Imprint receives, or
  tiles large inputs itself, remove the banding and pass whole images again.
- If real captures show lines taller than 200 px (large headings) split at seams,
  widen the overlap.
- If the text-direction classifier is seen flipping lines in real screenshots, turn it
  off for images in a new record; scanned PDFs may still need it.

## Validation

- `tests/test_ocr_runtime.py` drives a fake engine that reports every line a band
  shows, including lines cut by its edges: a 6,100 px image is read in four bands no
  taller than 2,000 px, every line comes back exactly once (a 190 px heading on the
  first seam included), the bands cover the image and overlap by at least 200 px, and
  a 2,000 px image is still one call.
- Real RapidOCR 3.10.0 on the Windows install, with the figures above: the synthetic
  12,000 px page (classifier off for that run, to isolate banding) and the real
  Chromium full-page capture (classifier on, as shipped).
- Not run: the Tesseract path, macOS and Linux runtimes, and PDFs with pages longer
  than 2,000 px after rendering.
