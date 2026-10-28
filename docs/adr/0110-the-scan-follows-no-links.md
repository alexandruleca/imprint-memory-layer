---
title: ADR 0110 — The scan follows no links
description: The directory scan lists no symbolic link or Windows junction, so every listed file lies under its root without a resolved path per file.
---

- Status: Accepted
- Date: 2026-10-28
- Amends: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) — the directory scan keeps files inside the selected root by following no links instead of resolving each file
- Related: [ADR 0107](0107-git-lists-a-repositorys-files-wsl-repositories-through-their-distribution.md)

## Context

A dashboard ingest of `\\wsl.localhost\Ubuntu\home\alexandruleca\code` (44 projects) on the installed Windows runtime showed "Found 44 projects" and then nothing for over ten minutes. The Python indexer was alive: it had used 111 s of CPU and made about 6,500 metadata operations per second, with no file reads and no model loaded. It was still in the scan, which writes `data/progress.json` only when it ends.

`scan_dir` called `allowed_source(file, root)` for every candidate file, which resolves both the file and the root. Resolving a path over `\\wsl.localhost` cost about 23 ms from the runtime's Python 3.12.8, and the walk also resolved every subdirectory to compare it with the excluded sub-project roots. Listing a directory over the same share cost about 0.15 ms per entry. (The log stayed silent for a separate reason: Python block-buffered the job's stdout into the dashboard's pipe. That was fixed as a routine queue fix, under [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md).)

The resolution existed for ADR 0005's rule that symlinks resolving outside the root are rejected. Over `\\wsl.localhost` it could not do that job: from Windows, a Linux symlink (to a file or a directory) lists as a plain file, and `realpath` returns its path unchanged.

## Decision

- The scan lists directories with `os.scandir` and skips every entry that is a symbolic link or, on Windows, a junction (reparse tag `IO_REPARSE_TAG_MOUNT_POINT`). Both facts come from the directory listing itself, so the check needs no filesystem call. Since the walk follows no link, every file it lists lies under the root it scans and no file is listed twice. It resolves no path.
- The scan applies only the secret-name rule (`secret_name`) to the files it lists. `allowed_source`, which resolves the path, stays for a single file named on the command line.
- Excluded sub-project roots are compared as paths relative to the scanned root, computed once per scan. They come from the same `find_projects` call as the root, spelled from the same resolved root.

## Rejected alternatives

- **Resolve the root once and keep resolving each file.** It halves the cost, still about 11 ms per file over the share, and the per-file resolution detects nothing there because Linux symlinks are not links from Windows.
- **Resolve only the files that are links.** It keeps indexing a link that points inside the root, which indexes the same file twice under two names (a `CLAUDE.md` link to `AGENTS.md`, for example), and keeps two rules for one walk. The walk already skipped directory links (`os.walk` without `followlinks`).
- **Keep `os.walk` and check `os.path.islink` per file.** `os.walk` discards the directory entries, so each check would be another round trip over the share.

## Consequences

- From the Windows runtime, scanning all 44 projects of that tree (40,906 files) takes 12.6 s, including one `wsl.exe` git listing per repository. On two of the projects, the old scan took 52.8 s (2,657 files) and 16.8 s (1,013 files); the new one 0.5 s and 0.3 s, with identical file lists.
- A symlinked file inside a root is no longer indexed under the link's name; its target is indexed where it lies if the scan lists it there. A later ingest or refresh cleans the source recorded under the link name as stale.
- Over `\\wsl.localhost`, a Linux symlink still lists as a plain file, so it is indexed through the link as before. That was already the case: resolution saw no link there either.
- A Windows junction is no longer entered. Before, the walk entered it and dropped files resolving outside the root; files reached through a junction pointing inside the root were indexed twice.

## Invalidation conditions

- If indexing a link's target under the link's name becomes a requirement (for example a project that keeps shared docs outside its root behind a link), then reopen per-link resolution for links only.
- If Windows gains a way to see Linux symlinks over `\\wsl.localhost`, then they are skipped there too, and this record's note on the share no longer applies.
- If another link-like reparse point (other than symlinks and junctions) turns out to lead a scan outside its root, then add its tag to the skipped entries.

## Validation

- Linux (WSL2, Python 3.12.8 test venv): `tests/test_indexing_retrieval.py` covers a scan that lists no symlinked file or directory, inside or outside the root, and no secret-named file; a scan with an excluded sub-project that resolves no path (`os.path.realpath` and `Path.resolve` patched to fail); and the existing git-listing tests. `test_indexing_retrieval`, `test_audio_ingestion`, `test_queue_priority`, `test_locale_encoding` and `test_projects` pass.
- Linux: the old and new scans of all 44 projects under `/home/alexandruleca/code` return the same 40,906 files.
- Windows (the installed runtime's Python 3.12.8 venv, this checkout on `PYTHONPATH`): the timings above, and identical file lists from the old and new scans for the two projects measured both ways.
- Not executed: the junction test (`test_scan_follows_no_junctions`) runs only on Windows and was not run here; a full `imprint ingest` with the new code; macOS.
