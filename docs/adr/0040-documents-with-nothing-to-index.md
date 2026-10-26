---
title: ADR 0040 — Documents with nothing to index
description: A document without extractable text is skipped with a reason instead of failing the run, and the result is remembered per content hash and extraction profile so it is retried only when that can change it.
---

- Status: Amended
- Date: 2026-10-24
- Amended by: [ADR 0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md) — OCR defaults, tool detection and runtime error handling
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) (source lifecycle), [ADR 0015](0015-workspace-roots-and-source-purge.md)
- Restructured: 2026-10-25

## Context

The owner ingested `C:\Users\alexa\Desktop`. The job exited 1 with *Indexing Incomplete* because of one scanned PDF: `Cerere-Rotatie-Stagiu_compressed.pdf: No indexable text extracted; prior chunks retained`. `imprint/cli_index.py` raised whenever a document's extraction produced no chunks. Raising protected the chunks of an earlier version from being replaced by nothing, but it treated a scanned or image-only document as an error. OCR is opt-in (`ingest.ocr_enabled`, which needs tesseract and poppler), so without OCR such a file can never yield text. Every later refresh read the file again, failed again and exited nonzero.

Refresh decides that a file is unchanged from its chunk payloads (`source_hash` on stored points, `vectorstore.get_source_states`). A file that produced no chunks has no payload, so the existing per-source state cannot remember that it was already examined.

## Decision

**No text is an outcome, not a failure.** When a document's extractors return no text, ingestion and refresh skip it and list it with a reason, and the run succeeds:

- A PDF with OCR off: *no text layer (scanned or image-only). OCR is off: `imprint config set ingest.ocr_enabled true` (needs tesseract and poppler)*.
- A PDF with OCR on but its tools missing: *OCR cannot run: install tesseract, poppler and the pytesseract and pdf2image packages*.
- A PDF with OCR on and its tools present: *no text found, even with OCR (lang)*.
- Any other document: *no extractable text*.

Single-file ingestion reports *Nothing to index* and exits 0. Real failures stay failures and keep the prior chunks: unavailable extractors, parse errors, undecodable or oversized text.

**A document that loses its text keeps its earlier chunks, and this is not a failure either.** The run lists it with *kept the chunks of its earlier version*. Its bytes and the extraction profile determine the result, so failing would repeat on every run without new information. Nothing is lost, and the remedies are explicit: enable OCR, or purge the source. Empty text files keep their behavior: an empty text file is verifiably empty, so its old chunks are deleted. A document without a text layer may still hold the text it had before, in a form this profile cannot read.

**Results are remembered.** `data/extraction-results.sqlite3` has the table `no_text(source_hash, profile, reason, checked_at)`. `profile` is the extraction profile as JSON: whether OCR is on, whether kreuzberg is used, and, when OCR is on, the OCR language and whether `tesseract`, `pdftoppm`, `pytesseract` and `pdf2image` are installed. Refresh and ingestion skip a document whose current content hash and profile are recorded. A changed file has a new hash. Changing OCR settings, or installing the OCR tools, gives a new profile. Either one makes a stored result no longer match, so the document is read again. `--force` on ingest or refresh bypasses the stored results. Rows hold a content hash and a reason, never paths or text.

**Shared by every workspace.** Workspace is not an input of the result: the same bytes have no text under the same profile in any workspace. The file is therefore not per workspace (unlike `imprint_graph_{ws}.sqlite3`). The same scanned file ingested into two workspaces is read, or OCRed, once. Wiping or deleting one workspace, and purging a source, leave it. Its entries remain true and hold no workspace data. `imprint wipe --all` removes it with the rest of the data, and uninstall without `--keep-data` removes the data directory.

## Rejected alternatives

- **Keep failing.** One unreadable file fails every run forever, which hides real failures.
- **Skip without remembering.** Every refresh reads the document again, and with OCR enabled it rasterizes and OCRs every page again.
- **Store a marker point in Qdrant.** It needs a vector, and it would show up in search, counts and facets.
- **One results file per workspace.** It duplicates OCR work and gains no isolation, because results contain no workspace data.
- **Delete the earlier chunks of a document that lost its text.** That loses text the document may still contain in a form the current profile cannot read.
- **Fail only when earlier chunks are kept.** That repeats on every run for as long as the file is unchanged.

## Consequences

- The Desktop ingest succeeds and names the scanned PDF and the OCR setting that would read it.
- The dashboard job succeeds (exit 0). Its output lists the documents with nothing to index, and the progress card counts them as skipped.
- Installing tesseract or poppler while OCR is enabled retries recorded documents on the next refresh.
- The PDF extractor still treats an OCR runtime error as "no text". If tesseract crashes on a file while the tools are present, the result is recorded as *no text found, even with OCR*. `--force` retries it.
- *Cleaned* in the run summary now counts only sources whose chunks were actually deleted.

## Invalidation conditions

- If an extraction outcome comes to depend on an input outside the content hash and the recorded profile, remembered results can skip a document wrongly: add the input to the profile or revisit the store.
- If the PDF extractor can tell an OCR runtime error from an empty result, crashes must stop being recorded as no text.
- If results ever need to hold workspace data, the shared file is no longer appropriate.

## Validation

- `tests/test_projects.py` (`NoTextDocumentTests`) covers:
  - A scanned PDF is skipped with the OCR reason while the run completes.
  - The next refresh reads no document.
  - Enabling OCR reads only that document again.
  - A document that loses its text keeps its earlier chunks.
  - A single scanned file reports *Nothing to index*.
- The full Python suite and `go build ./...` / `go vet ./cmd/` passed on Linux.
- Not run: real OCR with tesseract and poppler installed, and on Windows, where tesseract is often not on `PATH`. With OCR enabled, `pytesseract` uses the same lookup that the extraction profile uses.
