---
title: ADR 0197 — Restore writes indexed files back from their snapshots
description: imprint restore writes the files this device indexed under a folder, or every file of a project from any device, back from their source snapshots; it previews by default, writes only what the destination lacks unless asked, reports each file as exact, redacted or differing, writes documents as their extracted text and refuses paths that leave the destination.
---

- Status: Accepted
- Date: 2026-11-22
- Related: [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) (redaction placeholders), [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md) (the queue lock restore does not take), [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) (a folder's workspace), [ADR 0081](0081-verifiable-redacted-source-snapshots.md) (snapshots), [ADR 0195](0195-data-files-are-kept-whole-and-found-by-a-digest.md) (data files restore exactly), [ADR 0196](0196-a-file-gone-from-disk-keeps-its-snapshot.md) (deleted files keep their snapshots), [source memory](../source-memory.md)

## Context

After a WSL wipe the owner rebuilt lost plan files from Imprint's source snapshots by reading them back with `read_source`, page by page, and writing them out by hand. No command did it. ADR 0195 made large data files restore byte for byte, and ADR 0196 keeps a deleted file's snapshot instead of pruning it, so a whole folder can now be written back.

Three properties of snapshots shape such a command:

- They hold redacted text (ADR 0060), so a file with a secret cannot come back exactly.
- Extracted documents (PDF, Office, images read by OCR, audio transcripts) hold only the text read out of them.
- Their provenance, the original path included, also arrives from paired devices, so a path in a snapshot is untrusted input.

## Decision

1. **`imprint restore <dir> [--to <out>]`** restores the heads whose recorded `source_path` lies under `<dir>` and that this device recorded (`device.local_source`). Both paths are resolved, as pruning compares them. Each file goes back to its original path, or under `<out>` at its path relative to `<dir>`. The workspace is `--workspace`, else the folder's (ADR 0072), else the active one.
2. **`imprint restore --project NAME --to <out>`** restores every file head of the project, from any device, under `<out>` at its path within the project: `source_relative_path`, or the source key without its project. `--to` is required, since another device's absolute path means nothing here.
3. **Preview by default; `--apply` writes.**
   - Only destinations that do not exist are written.
   - `--all` also compares those that exist: an identical file is reported unchanged, a different one is kept unless `--overwrite` is given.
   - When two heads restore to one path, the newer one is written and the other reported.
4. **What is written depends on the snapshot.**
   - **Kind `text`** (code, text, data files kept whole): the text as UTF-8 bytes with its line endings as stored. Its SHA-256 is compared with the recorded `source_hash`, and the file is reported as exact (its modification time is restored too), as holding redacted secrets (with their count), or as differing.
   - **Documents** (kind `extracted`; an audio file's transcript, kind `conversation` with `media: audio`; a spreadsheet kept whole, whose text is extracted): the text, as `<name>.extracted.txt` beside where the document was. It is written only when the document itself is not at the destination, and never under the document's own name.
   - **Conversations, URLs and inline content** are not files: counted and skipped.
5. **Paths are untrusted.**
   - A project-relative path goes through `device.relative_path`, which refuses absolute paths, drive letters, `..` and NUL.
   - Every destination is resolved and must lie inside the destination folder, so a link or junction inside it cannot lead out. A destination that is itself a link is refused.
   - A refused file is an error.
6. **Writing is all or nothing per file.** Each file is written to a temporary file in its folder, then renamed over the destination. Restore never writes over a file that appeared meanwhile, and a replaced file keeps its permissions. Long Windows paths go through their extended-length spelling (ADR 0147).
7. **Output** lists up to 200 files, errors first, then totals. Restore exits nonzero when anything was refused or failed.
8. **No queue lock.** Restore reads snapshot heads (one SQLite read each, validated by its checksums) and writes outside the data folder. A refresh running meanwhile replaces a head atomically (ADR 0081), so restore writes either the old or the new version of a file. It is a CLI command only, not in the dashboard's command catalog.

## Rejected alternatives

- **Write every snapshot under its file's own name.** A PDF, DOCX or XLSX would be replaced by plain text under a name that promises the format: a broken file that looks restored.
- **Restore another device's files to their recorded absolute paths.** A path from synced data would choose where this device writes, possibly a path of another operating system. `--project` places them under a folder the user names.
- **Overwrite, or rewrite every file, by default.** A snapshot is the file as of its last refresh. A file that survived the loss may hold newer edits, and overwriting it would lose them.
- **Take the queue lock, like scrub.** Restore would wait behind long ingests (ADR 0064) for work that changes no memory, and nothing it does conflicts with them.
- **Offer restore in the dashboard or as an MCP tool.** Both would write into the user's folders from a server process or an agent session. The case at hand is a person rebuilding a folder from a terminal after a loss.

## Consequences

- A lost folder comes back in one command. Secrets come back as placeholders, counted in the report, and documents as their text.
- A file edited after its last refresh restores as of that refresh; the report cannot tell.
- A data file that was not UTF-8 holds extracted text under kind `text` (ADR 0195). It is written under its name and reported as differing.
- A head replaced by a paired device's copy is not local: a full-workspace import from a release without delta sync, or a bundle import, does that. `<dir>` mode skips such heads; `--project` restores them.
- Restored files inside a scanned folder are indexed by the next refresh as new versions, placeholders included.
- A snapshot of a file indexed only as legacy chunks does not exist, so restore cannot write that file (`refresh --force` creates snapshots, ADR 0081).

## Invalidation conditions

- If snapshots gain version history, let restore choose a version or a date.
- If snapshots ever keep a document's original bytes, write documents under their own names.
- If restoring from the dashboard or an agent is wanted, add it with the same path refusals and the dashboard's confirmation.
- If restore and a refresh of the same folder are found to interfere in practice, take the queue lock.

## Validation

- `tests/test_restore.py`, `RestoreTests` (9 tests), on the Windows install's Python 3.13 venv from a native copy, covers:
  - **Preview and apply:** a preview writes nothing; `--apply` restores a CRLF, non-ASCII file byte for byte with its modification time, counts a redacted GitHub token, and leaves no temporary file.
  - **Existing files:** they are left alone, compared with `--all`, and replaced only with `--overwrite`.
  - **Destinations:** `--to` writes at the same relative paths; `--project` writes another device's file under `--to`, which `<dir>` mode skips; `--project` without `--to` is a usage error.
  - **Refusals:** `..`, absolute and drive-letter paths are refused and the exit code is 1. A junction inside `--to` that leads outside is refused (a symbolic link on POSIX).
  - **Documents:** an extracted PDF and a kept-whole spreadsheet are written as `.extracted.txt`, not once the PDF is back; a conversation is skipped.
  - **Conflicts:** the newer of two heads of one path is written.
- Go 1.26.11 on WSL (linux/amd64): `go vet ./...` and `go test ./...`, including `TestRestoreArgsMakeTheFolderAndDestinationAbsolute`.
- **End to end on Windows 11:** `imprint.exe` cross-compiled for windows/amd64 and run against a disposable data folder that ingest and refresh had filled.
  - It resolved the folder's workspace from its approved root.
  - A preview wrote nothing; `--apply` restored a deleted folder, exact apart from the counted secret.
  - `--project` without `--to` exited 2; with `--to` it wrote the project's files.
- **Not run:** the binary against the live install, macOS, and a Windows runtime restoring a WSL folder through `\\wsl.localhost`.
