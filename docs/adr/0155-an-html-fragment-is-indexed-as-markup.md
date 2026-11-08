---
title: ADR 0155 — An HTML fragment is indexed as markup
description: A local HTML file without a doctype or an html, head or body element is a template or fixture, indexed verbatim as source code; a page keeps readability extraction, and a page without text has nothing to index.
---

- Status: Accepted
- Date: 2026-11-08
- Related: [ADR 0040](0040-documents-with-nothing-to-index.md) (a page without text is a document with nothing to index), [ADR 0156](0156-a-file-whose-bytes-are-not-its-format-is-an-outcome.md) (the other errors of the same run), [indexing and retrieval](../indexing-retrieval.md)

## Context

The owner ingested 37 projects (43,331 files, among them the Shopware platform and Shopware Business Platform repositories). The run printed `[error] …: html parse produced empty output` for about 120 HTML files, most of them `*.component.html`, ended *Indexing Incomplete* and exited 1. The next run read and failed on the same files again, because a failed file stores no state.

`imprint/extractors/html.py` read every `.html` and `.htm` file as a web page: trafilatura, then BeautifulSoup `get_text()`, then tag stripping, and it raised `ExtractionError` when no text remained. The failing files are Angular component templates and test fixtures: markup with bindings and no prose. `src/app/shared/components/star/star.component.html` is one `<account-sw-icon icon="star" [size]="size" … />` element.

Measured on the 1,058 HTML files under the Business Platform's Angular apps (`src/app/**`), none of which has a doctype or an `<html>`, `<head>` or `<body>` element: the page reading failed on 110 and indexed 948 as stripped text. The other failing fragments were Storefront test templates, CMS block preview fixtures and the Administration's loading-screen partial. The stripped text drops what a template says. `menu.component.html` became its control-flow lines and `{{ section.label | translate }}`; its `<ngx-masonry [options]="masonryOptions">` and `<sbp-menu-item [name]="child.label" [icon]="child.icon" (click)="openItem(child)">` elements became blank lines. The `.ts` component beside each template, and `.vue` and `.svelte` files, are indexed verbatim as code.

Two full pages in the same run, `Components/docs.html` and `Components/Core/api-doc/index.html`, are rapi-doc shells: a script tag and a `<rapi-doc spec-url=…>` element, with no text either.

## Decision

**A fragment is source.** A local `.html` or `.htm` file with no doctype and no `<html>`, `<head>` or `<body>` element is a fragment: a component template (Angular, Vue-style, a server-side include) or a test fixture. It is indexed verbatim, markup, bindings and text alike, with the code chunk targets, `source_format: html_fragment` in its metadata. The check is a case-insensitive search of the file's bytes for `<!doctype html`, `<html`, `<head` or `<body` followed by whitespace, `>` or `/`, so `<header>` is not taken for `<head>`.

**A page keeps readability extraction**, as prose. A page without text, such as a JavaScript-rendered shell, returns no text, so [ADR 0040](0040-documents-with-nothing-to-index.md) applies: it is skipped with *no extractable text*, remembered, and the run succeeds.

**Downloaded HTML is unchanged.** `ingest-url` and other byte-based extraction (`dispatch_by_mime`) always read a page, and a page without text there stays an error that the URL ingest reports.

## Rejected alternatives

- **Fall back to the markup only when the page reading finds no text.** This removes the 110 failures, but the 948 templates that contain some text stay stripped text without their elements and bindings, as `menu.component.html` shows. The same kind of file would get two representations depending on whether it happens to contain a translation pipe.
- **Index every `.html` file as code.** Saved web pages and exported documentation, which the HTML extractor and `html` in `ingest.doc_formats` exist for, would be indexed as markup and scripts instead of their text.
- **Skip fragments as documents with nothing to index.** The errors would go away, but the templates of a frontend, which are its source, would be missing from the index.

## Consequences

- Templates are found by their component names, bindings, inputs and translation keys, and their chunks cite the exact source span like other code.
- Fragments that failed before have no stored state, so the next ingest or refresh indexes them. Fragments already indexed as stripped text keep those chunks until the file changes; `imprint ingest --force` or `imprint refresh --force` on the project rereads them as markup.
- The rule is textual. A fragment that mentions `<body>` in a comment or code sample is read as a page. A page that omits the doctype and all three elements, which HTML allows, is indexed as markup.
- The extractor reads the whole file to check for the markers, as it already read the whole file to extract it; `ingest.max_doc_size_mb` still bounds it.

## Invalidation conditions

- If pages without a doctype or an `<html>`, `<head>` or `<body>` element turn up indexed as markup in practice (saved pages, generated documentation), then the page markers are not enough and the rule needs more evidence than those four.
- If retrieval over templates is measured to be worse as markup than as stripped text, then reopen the representation.
- If downloaded or captured HTML starts carrying fragments (a browser capture of one element, for instance), then the byte-based path needs the same rule.

## Validation

- `tests/test_malformed_documents.py` (`HtmlTests`): Angular, fixture and `<header>` fragments are indexed verbatim as code; a rapi-doc shell page has no text; a page with text is prose; a downloaded page without text is still an error. These tests fail on the previous extractor.
- Run on macOS with the installed runtime's Python 3.12 against the Shopware checkout: all 1,058 Angular HTML files read without error, 1,056 with content and 2 that are 0-byte files (no text, ADR 0040); `docs.html` and `api-doc/index.html` return no text.
- Not run: a complete `imprint ingest` of the 37 projects, and Windows and Linux. The change is in pure Python with no platform-specific path handling.
