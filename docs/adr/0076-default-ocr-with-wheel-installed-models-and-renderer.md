---
title: ADR 0076 — Default OCR with wheel-installed models and renderer
description: Enable OCR by default and install its models and PDF renderer with the base runtime instead of requiring separately installed system tools.
---

- Status: Amended
- Date: 2026-10-26
- Amends: [ADR 0040](0040-documents-with-nothing-to-index.md) — OCR defaults, tool detection and runtime error handling
- Amended by:
  - [ADR 0109](0109-automatic-ocr-runtime-repair.md) — automatically repair missing bundled OCR dependencies
  - [ADR 0111](0111-intel-macs-are-not-supported.md) — the Intel macOS OpenCV 4.11 pin is gone

## Context

The owner reported that enabling OCR fails because its dependencies are absent and requested OCR on by default with automatically installed dependencies. The base requirements commented out every OCR package; even installing those bindings left Tesseract and Poppler as unmanaged prerequisites. PDF extraction swallowed OCR runtime errors and could cache a tool failure as an empty document.

## Decision

Install pinned RapidOCR 3.10.0 with its wheel-bundled small models, Pillow and PDFium through the hashed base requirements. Use the existing single ONNX Runtime overlay. Pin OpenCV 4.11 on Intel macOS because newer Intel wheels require macOS 14 while our baseline is 13. Enable `ingest.ocr_enabled` by default while preserving explicit saved opt-outs. The default `eng` and `auto` language values use the bundled Chinese/English recognizer. Other language values retain a Tesseract compatibility path, with Python bindings installed but system Tesseract and language data managed by the user.

Render scanned PDFs one page at a time. Propagate OCR failures so they remain retryable errors; only a successful empty extraction is a remembered no-text result. Include the backend identity and availability in the extraction profile so earlier no-text results are retried after the backend changes.

## Rejected alternatives

- Installing only pytesseract and pdf2image: these bindings still depend on system executables absent from a fresh install.
- Keeping OCR opt-in: this does not satisfy the requested default behavior.

## Consequences

Default OCR needs no system package manager or separate model download. Setup downloads more Python wheels and model bytes. Bundled recognition covers Chinese and English, not every Tesseract language. Custom language selections keep their system dependency. OCR is CPU bounded to four inference threads. Release archives continue to provision dependencies during bootstrap rather than becoming offline installers.

## Invalidation conditions

If RapidOCR stops bundling its default models, replace or explicitly provision the models before upgrading. If supported platform wheels become unavailable, replace the backend or document a narrower platform scope with native validation. If multilingual recognition without system dependencies becomes required, select and validate additional bundled models before claiming that support.

## Validation

On macOS arm64, installed the hashed base and CPU requirements into a disposable venv and extracted the expected text from a generated PNG and an image-only PDF with real RapidOCR/PDFium. The targeted Python checks (146 tests, plus a separate embedded-Qdrant interruption/resume regression) and `go test ./cmd ./internal/...` passed. Universal base resolution and wheel metadata were inspected, including the Intel macOS 13 OpenCV constraint; this is not native execution on those targets. Native Windows, Linux and Intel macOS OCR execution and complete installers remain unverified in this session.
