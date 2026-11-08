---
title: ADR 0156 — A file whose bytes are not its format is an outcome, not a failure
description: A PDF or image its parser rejects as not that format is skipped with the reason and remembered like a document without text, and JSON that does not parse is indexed verbatim; other extraction errors stay failures.
---

- Status: Accepted
- Date: 2026-11-08
- Amends: [ADR 0040](0040-documents-with-nothing-to-index.md) — parse errors caused by bytes that are not the format leave the real failures and become remembered outcomes
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) (oversize and undecodable sources stay failures), [ADR 0155](0155-an-html-fragment-is-indexed-as-markup.md) (the HTML errors of the same run), [indexing and retrieval](../indexing-retrieval.md)

## Context

The same ingest of 37 projects as [ADR 0155](0155-an-html-fragment-is-indexed-as-markup.md) reported, besides the HTML templates, these errors from test fixtures, and the run exited 1:

- `pdf parse failed: Stream has ended unexpectedly` for `invoice1_document.pdf`, whose 5 bytes are `test\n`, and `pdf parse failed: Cannot read an empty file` for the 0-byte `contract_dummy.pdf`. pypdf also logged `invalid pdf header: b'test\n'` and three `EOF marker not found` warnings. With no logging handler configured, Python's last-resort handler printed them to stderr, over the progress line.
- `cannot identify image file <_io.BytesIO object at 0x134231760>` for `test-txt-as-png.png`, whose bytes are `I am not an image`, and for a 0-byte `icon.png` fixture. Pillow's `UnidentifiedImageError` left the extractor unwrapped, with a memory address in its message.
- `invalid JSON: Expecting value: line 1 column 1 (char 0)` for twelve 0-byte snippet fixtures in the Shopware platform's tests, and `invalid JSON: Expecting property name enclosed in double quotes` for `entity-schema-invalid.json`, a deliberately broken PHPStan fixture.

[ADR 0040](0040-documents-with-nothing-to-index.md) made a document without text an outcome but kept "parse errors" among the real failures. Each of these errors is decided by the file's bytes alone. Every run read the files, failed, and ended *Indexing Incomplete* again, which is what ADR 0040 rejected for scans: *one unreadable file fails every run forever, which hides real failures*. In this run three real limits (`ingest.max_file_bytes` on two chat exports and a 2 MB data file) were listed among about 140 errors.

Two tests added on 2026-10-28, when JSON with comments became readable, asserted that malformed JSON still fails: `test_invalid_json_still_fails_at_its_line` and `test_malformed_json_still_reports_an_error`. They guarded that the JSONC pass did not hide a syntax error. No record gave a reason for failing on one.

## Decision

**Bytes that are not the format are an outcome.** An extractor raises `UnreadableDocument`, a subclass of `ExtractionError`, when the format's parser rejects the bytes as not being that format:

- PDF: pypdf's `PdfReadError` family, including `EmptyFileError` and `PdfStreamError`, as *not a readable PDF: \<pypdf's message\>*. The family also holds `FileNotDecryptedError`: a password-protected PDF is reported as *encrypted PDF: it opens only with its password*, since Imprint has no way to supply one and no later run could read it either.
- Images: Pillow cannot identify the format (*not a readable image: unrecognized format*), or decoding fails with `OSError` or `SyntaxError` (truncated or corrupt data). The image is decoded before OCR runs, so an OCR error is never taken for damage.

Ingest and refresh treat it as [ADR 0040](0040-documents-with-nothing-to-index.md)'s nothing to index: the file is listed under *No text* with its reason, recorded per content hash and extraction profile in `data/extraction-results.sqlite3` so that later runs do not read it, and the run succeeds. A file that had chunks keeps them. Single-file ingestion reports *Nothing to index*. `--force` reads it again.

**JSON that does not parse is text.** When a `.json` or `.jsonc` file parses neither as JSON nor as JSONC, it is indexed verbatim as code, with `source_format: invalid_json` and the parser's message in `json_error`. A source file with a syntax error is still source: `.ts`, `.yaml` and `.toml` files are indexed without being parsed, and `tsconfig*.json` is already kept verbatim. An empty JSON file has no text, so ADR 0040 applies.

**Everything else stays a failure.** Other parser exceptions (a pypdf error on a file it recognized as a PDF, an OCR runtime error, Pillow's decompression-bomb refusal), unavailable extractors, read errors, undecodable text and the size limits `ingest.max_file_bytes` and `ingest.max_doc_size_mb` still keep prior chunks and make the run incomplete, as [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) and ADR 0040 decided. Callers that catch `ExtractionError` (`ingest-url`, desktop export learning) see `UnreadableDocument` as before.

**pypdf's warnings are not printed.** The PDF extractor sets the `pypdf` logger to `ERROR`. Those warnings report damage pypdf recovers from; what it cannot recover from arrives as an exception.

## Rejected alternatives

- **Keep failing,** as the 2026-10-28 tests asserted. The run above shows the cost: the same fixture errors on every run, an exit code of 1, and the size-limit failures that need the owner's action buried among them.
- **Skip without remembering.** Every run would read and parse the files again and list them again. ADR 0040 rejected the same for scans.
- **Treat every extractor exception as unreadable.** A parser bug on a valid but unusual file, or a broken OCR runtime, would be recorded as a property of the file and not retried after the library is fixed, because parser versions are not part of the extraction profile. Only the parsers' own "this is not the format" errors qualify.
- **Skip malformed JSON like a broken PDF.** It is readable text, and a fixture's content is what a search for it should find.

## Consequences

- On that run's projects, the PDF, image and empty JSON fixtures become *No text* entries that later runs skip, `entity-schema-invalid.json` is indexed, nothing is printed over the progress line, and only the three size-limit files remain failures.
- Parser versions are not part of the extraction profile. A PDF that pypdf rejects stays recorded after a pypdf upgrade that might read it, until the file or the profile changes or `--force` runs.
- The `no_text` table of `data/extraction-results.sqlite3` now also holds these reasons. Its schema is unchanged.
- Desktop export learning indexes an export JSON file that does not parse verbatim, where it used to report *Extraction failed*.

## Invalidation conditions

- If a parser upgrade starts reading files it rejected as not being its format, then add parser versions to the extraction profile so recorded results are retried.
- If Pillow raises `OSError` while decoding for a reason in the environment rather than in the file (a decoder missing from its build), then such images are recorded wrongly and the image rule must be narrowed.
- If verbatim malformed JSON fills search with large broken exports, then cap or skip it.

## Validation

- `tests/test_malformed_documents.py` (`PdfTests`, `ImageTests`): non-PDF and empty bytes are unreadable and log no pypdf warning; a password-protected PDF is unreadable with its own reason; another pypdf error stays an `ExtractionError`; text and nothing under an image name, and a truncated PNG, are unreadable; an OCR error on a valid image propagates. `tests/test_json_doc.py` and `tests/test_json_config_ingestion.py`: malformed JSON is kept verbatim with its error line, and an empty file has no text. `tests/test_projects.py` (`NoTextDocumentTests`): an unreadable document completes the run with its reason and is not read on the next run, and a single unreadable file is *Nothing to index*. These tests fail on the previous extractors.
- Run on macOS with the installed runtime's Python 3.12 (pypdf 6.21.2, Pillow 13.0.0) against the Shopware fixtures named above: each produced the error quoted in the context before the change, and an outcome and no stderr output after it.
- Not run: a complete `imprint ingest` of the 37 projects, and Windows and Linux. The change is in pure Python with no platform-specific path handling.
