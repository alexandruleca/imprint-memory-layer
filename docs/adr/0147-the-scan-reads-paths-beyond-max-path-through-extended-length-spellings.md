---
title: ADR 0147 — The scan reads paths beyond MAX_PATH through extended-length spellings
description: On Windows, ingest and refresh list and read a path of 240 characters or more through its \\?\ spelling, and keep the usual spelling for everything they compare, show or store.
---

- Status: Accepted
- Date: 2026-11-07
- Scope: directory and single-file `ingest` and `refresh` on a Windows runtime; real Windows hosts are unvalidated (see Validation)
- Related: [ADR 0107](0107-git-lists-a-repositorys-files-wsl-repositories-through-their-distribution.md), [ADR 0110](0110-the-scan-follows-no-links.md), [indexing and retrieval](../indexing-retrieval.md)

## Context

On 2026-11-07 `imprint ingest` of `/home/alexa/code` in a WSL distribution, run by the Windows runtime, stopped during the scan:

```
Cannot scan: [WinError 3] The system cannot find the path specified: '\\wsl.localhost\Ubuntu-26.04\home\alexa\code\deepstash\mobile-apps\apps\deepstash\components\progress-list\components\progress-list-suggest-achievement-item\components\progress-list-suggest-achievement-item-button\components\progress-list-suggest-achievement-item-button-with-icon'
```

That directory is 280 characters long through `\\wsl.localhost\Ubuntu-26.04`, and 250 as the Linux path. Win32 file calls stop at MAX_PATH (260) unless the machine turns on `LongPathsEnabled`, which Windows leaves off. Python's own manifest already declares long-path awareness, so the registry setting is the only thing missing. Reading a WSL tree through `\\wsl.localhost` adds about 30 characters to every path, so trees that Linux lists without trouble go over the limit. A directory the scan cannot list stops `ingest` and `refresh` (`scan_dir` raises, `cli_index` exits 1).

## Decision

**On Windows, every filesystem call the source pipeline makes on a path of 240 characters or more uses its extended-length spelling.** That is `\\?\UNC\server\share\…` for a UNC path such as `\\wsl.localhost\…` and `\\?\C:\…` for a drive path, normalised as such a spelling requires. [`imprint/long_paths.py`](../../imprint/long_paths.py) does this:

- `os_path` spells a path for one call. A shorter path, a path already spelled that way, and every path on Linux and macOS are returned unchanged. The 20-character margin below MAX_PATH covers the `\*` a directory listing appends and the 248-character limit of directory creation.
- `plain` takes the prefix off again, and `realpath` resolves through the long spelling and returns the usual one.

The calls are the scan's directory listing, its `.git` lookup and root check, `fingerprint`'s stat and read, the text read, the modification time, the document extractors, and the existence check when sources are pruned. **Every path the pipeline compares, shows or stores keeps its usual spelling:** what `scan_dir` returns, `source_path`, exclusions and project mappings. Short paths are spelled exactly as before.

## Rejected alternatives

- **Only the registry setting.** It needs an administrator, applies to the whole machine, and leaves every other machine that reads a deep WSL tree failing the same way. It is still worth turning on, and the code works either way.
- **Skipping folders the scan cannot list.** The files in them would be silently missing from the index. That is a separate choice about scan errors (non-git walks propagate them, ADR 0107), not a fix for long paths.
- **The extended-length spelling for every path on Windows.** `realpath` of a prefixed path keeps the prefix, so every stored `source_path` would change spelling. `unchanged` compares `source_path`, so the next refresh would read every indexed Windows source again. It would also send prefixed paths to code that has only ever seen the usual spelling. Prefixing only paths that fail today changes nothing for paths that work.
- **Changing the working directory or using relative paths to stay under the limit.** The process's working directory is shared by every thread of the dashboard, and the limit applies to the full path Windows builds from it.

## Consequences

- Repositories with deep trees, typical of JavaScript monorepos, can be ingested and refreshed from a WSL distribution by the Windows runtime without changing the machine.
- A source whose path is 240 characters or more is recorded under its usual spelling, as any other. Before this change such a source could not be read, so no stored record changes.
- Extractors receive the prefixed spelling for long paths. Most read the bytes with Python's `open`. Kreuzberg and the audio decoder pass the path to native code, which has not been tried with such a spelling.
- `imprint learn`, `purge_sources` and the other readers outside the source pipeline still use usual spellings.

## Invalidation conditions

- If Windows enables long paths by default, or Imprint's installer turns `LongPathsEnabled` on, then the prefix is redundant and `long_paths` can be removed.
- If an extractor fails on a prefixed path, then give that extractor the bytes instead, or exempt it and record the limit.
- If conversations or purged sources hit MAX_PATH, then route those readers through `os_path` as well.

## Validation

- **`tests/test_long_paths.py`:**
  - the 280-character path from the report gets the `\\?\UNC\` spelling, and `plain` restores it;
  - a long drive path is normalised to a `\\?\C:\` spelling;
  - short paths, prefixed paths and Linux and macOS paths are unchanged;
  - with a stand-in for Windows where only the long spelling can be opened, `scan_dir` lists the tree and returns the usual paths, and `fingerprint` reads the file, records the usual `source_path`, and `missing_sources` prunes it only once it is gone.
- `tests/test_indexing_retrieval.py`, `tests/test_device_provenance.py` and `tests/test_source_snapshots.py` pass unchanged on Linux.
- **Not verified:** a real ingest of the reported tree on Windows through `\\wsl.localhost`, and Kreuzberg and audio extraction of a file beyond MAX_PATH.
