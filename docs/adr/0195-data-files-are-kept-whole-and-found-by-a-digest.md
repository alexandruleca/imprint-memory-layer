---
title: ADR 0195 — Data files are kept whole and found by a digest
description: A large JSON, CSV, TSV or XLSX file, or one in a folder of fixtures, generated output, training data or translations, is stored as a few chunks holding its exact text, each starting with a digest of the file; search finds it by the digest, exact identifiers and phrases, and read_source restores the whole file.
---

- Status: Amended
- Date: 2026-11-22
- Amended by: [ADR 0201](0201-upkeep-brings-what-is-stored-up-to-date-in-the-idle-slot.md) — stored data files are kept whole in the idle slot, without waiting for the next ingest or refresh
- Related: [ADR 0081](0081-verifiable-redacted-source-snapshots.md) (snapshots and covering spans, unchanged), [ADR 0162](0162-long-tool-events-are-one-chunk-embedded-from-a-reduced-view.md) (the same long-chunk and reduced-view mechanism, for tool events), [ADR 0172](0172-exact-matches-survive-a-large-corpus.md) (lexical candidates and verbatim matches), [ADR 0156](0156-a-file-whose-bytes-are-not-its-format-is-an-outcome.md) (JSON that does not parse), [ADR 0176](0176-deletions-reach-paired-devices-that-hold-the-memory.md) (whose deletions reach peers), [configuration](../configuration.md)

## Context

The owner's default workspace was audited on 2026-11-22. It held 388,255 chunks from 72,297 sources. More than half were records of data files that nobody searches one record at a time:

| Data | Chunks | Share |
| --- | --- | --- |
| Regression outputs (`floorplan-importer/output/regress…`) | 78,808 | 20.3% |
| Test fixtures, snapshots and baselines | 71,976 | 18.5% |
| CSV/XLSX dumps, such as performance-test results | 20,933 | 5.4% |
| Training, evaluation and benchmark datasets | 13,341 | 3.4% |

The JSON extractor (`extractors/json_doc.py`) splits an array into one document per item, so the largest fixture alone was 24,987 chunks. JSON was 48% of all chunks, and 100,560 chunks were under 200 characters. These records also dominated the tags. The most used topic was `coordinate-data`, with 61,505 uses, almost all from fixtures. `json-processing` (162,647) and the project name `floorplan-importer` (140,936) were the top domains.

The owner wants these files kept, not deleted. They have already rebuilt lost plan files from Imprint's source snapshots after a WSL wipe, and want training data and fixtures to be restorable the same way, even if they are not searchable record by record. Today's snapshot of a JSON file holds the extractor's per-item rendering (`# Item 12\n\n**start:** …`), not the file, so it cannot restore JSON as it was.

A low-value filter already existed in `cli_index.py` (`_is_low_value`), but nothing called it.

## Decision

1. **Which files.** A file is kept whole when it is JSON, JSONC, CSV, TSV or XLSX and either:
   - it is larger than `ingest.data_file_min_kb` (64 KiB by default), or
   - it is larger than 16 KiB and its path has a folder named `fixture(s)`, `__fixtures__`, `__snapshots__`, `snapshot(s)`, `testdata`, `test-data`, `golden(s)`, `baseline(s)`, `output(s)`, `regress…`, `training`, `dataset(s)`, `seed(s)`, `dump(s)`, `locale(s)`, `i18n` or `translations`.

   Chat exports named `conversations.json` or `conversations-N.json` keep their per-conversation documents. `ingest.data_files` is `whole` by default; `records` indexes every file record by record, as before.

2. **Its text is the file's own.** The snapshot holds the file's text as read: UTF-8 JSON and CSV byte for byte, after redaction. An XLSX file, or a text file that is not UTF-8, holds its extracted text. `read_source` gives back the file.

3. **A few chunks, each starting with a digest.** The text is cut at line ends into contiguous pieces of at most 262,144 characters (ADR 0162's piece size). Each piece's content is the digest followed by its exact span. The digest is the piece's source prefix, so it is retrieval context, not source bytes, and the snapshot rebuilds the file exactly (ADR 0081). The digest has three parts:
   - the path;
   - a header line, `--- {"kind": "data_file", "format", "reason", "bytes", "chars", "digest": <its length>} ---`;
   - up to 2,400 characters describing the file. For JSON this is its shape (keys, array lengths, value types) and first record. For CSV/TSV it is the row count, columns and first rows. Otherwise it is a reduced start of the text.

   Records are typed `data` and carry `doc_metadata.data_file`.

4. **Found by the digest.**
   - **Vector:** a piece's vector is computed from its digest and a reduced sample of its data. Both embedding entry points apply this, as they do for ADR 0162's view.
   - **Keyword score:** coverage and term frequency count only the digest's words. Exact identifiers and verbatim phrases still match anywhere in the data.
   - **Detection:** the header carries the digest's length, so any device recognises a piece from its content alone.

5. **Stored files change form at the next ingest or refresh.** A stored file whose form differs from what its path and size now call for counts as changed: record chunks for a file now kept whole, or the reverse after switching to `records`. It is indexed again on the device that holds the file. Its old chunks are that device's own, so their deletion reaches paired devices (ADR 0176). Nothing converts without a run, and files no longer on disk are not touched.

6. `_is_low_value` is removed.

## Rejected alternatives

- **Purge the folders.** This loses what the owner wants restorable, and a purge deletes on every paired device (ADR 0176).
- **Skip data files at ingest by reviving `_is_low_value`.** A skipped file has no snapshot, so it cannot be restored. Its rules were also too broad: any path containing `messages`, `mock` or `locale`, and every JSON file over 5,000 characters, which would include ordinary configuration.
- **One summary memory beside a snapshot with no chunks.** ADR 0081 requires the manifest's chunks to cover the text and to exist in the index:
  - `read_source` verification would report every manifest chunk missing.
  - A new snapshot kind would fail `_validate` on older peers, which accept only `text`, `conversation`, `extracted`, `inline` and `url`.
- **A summary written by the local model.** ADR 0067 keeps the LLM off the ingest path. Such a summary would also differ between devices and runs, and would make ingest wait for the model for each of about 500 files.
- **Mark pieces with a new payload field instead of a header in the content.** Sync carries only `MEMORY_FIELDS`, so peers would see an unmarked 256 KB chunk. The header travels inside the content.
- **The size rule alone.** It leaves 153 files between 16 and 64 KiB in fixture, output, training and locale folders, 13,896 chunks, record by record.

## Consequences

**Effect on the owner's store.** Run with the real detection code over the audited sources:
- 499 files go from 195,244 chunks to about 944.
- The store goes from 388,255 chunks to about 193,955.
- By recording device, the chunks removed at that device's next refresh are:

| Device | Files | Chunks removed |
| --- | --- | --- |
| Northrend (Windows) | 435 | 171,262 |
| Outland.local | 63 | 23,042 |
| bedrock | 1 | 940 |

- Reasons: `output` 259, `large` 117, `fixtures` 68, `training` 23, translations and locales 17, `testdata` 11, `__fixtures__` 4.
- These files now restore exactly where they did not before.
- Topic and domain counts lose the per-record tags that dominated them.

**What search loses.**
- Dense search no longer reflects individual records.
- A query that names a value only inside the data finds the file only through an exact identifier (with a digit or underscore) or a verbatim phrase.
- A data file below the thresholds is still indexed record by record. This keeps small configuration findable setting by setting.

**Older and mixed devices.**
- Older devices store and sync the pieces as ordinary long chunks, as they do ADR 0162's.
- Their keyword score counts every word of a piece, so a piece can rank higher there until they update. It still appears once per 262,144 characters, not once per record.
- An older device that re-embeds a piece embeds its start, which is the digest.

**Not covered.**
- The kept-whole form applies to files scanned from disk by `ingest`/`refresh` and to single-file ingest.
- Browser uploads, URL ingest and `ingest_content` keep chunking as before.
- Data files larger than `ingest.max_doc_size_mb` are still refused.

## Invalidation conditions

- If questions about individual records of kept-whole files stop finding what was asked (in stored queries or recall evaluations), then set `records` for those projects, or embed per-record vectors without storing per-record chunks.
- If a folder name in the list holds data the owner searches by record, then remove it from the list or add a per-project override.
- If older devices remain in a workspace and kept-whole pieces crowd their keyword results, then send the digest boundary through sync, or delay conversion until every peer has updated.
- If source snapshots gain version history, then revisit whether conversion should keep the earlier record-by-record reading.

## Validation

- `tests/test_data_files.py` (16 tests, run on the Windows install's Python 3.13 venv from a native copy) covers:
  - **Detection:** folder and size rules, code, documents and chat exports unaffected, `records` mode.
  - **Form changes:** stored files change form in both directions.
  - **Pieces:** they rebuild a CRLF JSON file byte for byte; a file larger than a piece splits at lines without gaps; the snapshot validator accepts the pieces.
  - **Digest:** it names JSON structure, the first record and CSV columns. Secrets are redacted before the digest and the snapshot.
  - **Search:** the embedding view starts with the digest and stays within a chunk. Data words outside the digest score zero, while the digest and exact identifiers score. Case folding cannot move the digest boundary.
  - **End to end:** `ingest` then `refresh` in `records` mode, then `refresh` in `whole` mode, on an in-memory Qdrant. The snapshot restores the file exactly each time.
- The related suites pass: `test_indexing_retrieval`, `test_long_events`, `test_json_config_ingestion`, `test_source_snapshots`, `test_config_help`, `test_config_simple`, `test_mcp_output`, `test_docs_frontmatter`. The only failure in the first run was a cp1252 console encoding error in an existing print when output was piped to WSL; it passed with UTF-8 output.
- The dry-run figures above come from the real `reason()` applied to a payload dump of the live collection.
- **Not yet run:** a conversion on the live install, and a retrieval-quality run with the real embedding model.
