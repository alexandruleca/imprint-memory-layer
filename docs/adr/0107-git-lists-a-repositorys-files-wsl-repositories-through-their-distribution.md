---
title: ADR 0107 — Git lists a repository's files; WSL repositories through their distribution
description: Inside a git repository the scan indexes only what git lists, a WSL repository is listed by its distribution's git, and a repository git cannot list fails the scan.
---

- Status: Accepted
- Date: 2026-10-28
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md), [ADR 0026](0026-windows-installer-wsl-client-bridge.md)

## Context

`imprint ingest \\wsl.localhost\Ubuntu\home\…\tarom-forms` on the installed Windows runtime stopped with `PermissionError: [WinError 5] Access is denied` on `api\var\imports`. That directory is a root-owned Docker volume (mode 0750), and the repository's `api/.gitignore` ignores `/var/`.

Two things led there:

- `_git_tracked_files` returned `None`, which means "not a repository", whenever git was missing, timed out or exited nonzero. The Windows machine has no git, so the scan quietly walked the repository without its ignore rules. Walked that way, tarom-forms gives 5,311 files instead of the 1,045 git lists; 4,264 of the extra files are the Symfony cache in `api/var/cache`.
- Even with git, the walk listed every directory and dropped ignored files one by one, so it also entered `api/var/imports`. The same ingest failed on Linux. [Indexing and retrieval](../indexing-retrieval.md) already promised that the scan respects git's ignore boundaries, so this half was fixed as a bug in the same change: the walk now enters only directories holding a file git lists.

Installing Git for Windows would not cover WSL repositories: Git for Windows treats a repository whose files belong to another account (the Linux user, seen from Windows) as dubious ownership and refuses it without a `safe.directory` override. This is Git for Windows' documented behavior; it was not reproduced here because this machine has no Windows git.

## Decision

- A root is in a git repository when it, or a folder above it, holds a `.git` entry: a directory, or the file of a worktree or submodule. This is checked without running git, so a missing git cannot make a repository look like a plain folder.
- Inside a repository, the scan indexes what `git ls-files --cached --others --exclude-standard` lists, and the walk enters only directories that hold a listed file.
- On a Windows runtime, a root in a WSL distribution's namespace (`\\wsl.localhost\<distribution>\…` or `\\wsl$\<distribution>\…`) is listed by that distribution's git: `wsl.exe -d <distribution> --exec git -C <Linux path> ls-files …`. That git runs as the files' owner. Other paths, Windows drives included, use the runtime's own `git`.
- When git cannot list a repository (not installed, no answer within 30 seconds, nonzero exit), `ingest` and `refresh` stop before indexing and print one line naming the root and git's reason, and exit 1. A repository is never walked without its ignore rules.
- Folders outside any repository are unchanged: the walk applies the built-in filters and stops on a directory it cannot list.

## Rejected alternatives

- **Skip directories the walk cannot read.** The Windows ingest would then finish, but with the 4,264 ignored cache files indexed. The read error was a symptom of the missing ignore rules.
- **Bundle a minimal git (MinGit) with the Windows installer.** It would list repositories on Windows drives, but WSL repositories would still need `safe.directory` turned off. That check exists because a repository's own config (for example `core.fsmonitor`) can make git run commands as the Windows user. It also grows the installer.
- **Read the git index and ignore files in Python (for example with dulwich).** One implementation on every platform with no git process, but it adds a dependency and reimplements semantics the indexer deliberately leaves to git; the scan's comments already rejected an in-repository gitignore reimplementation.
- **Keep walking without git and print a warning.** The index would still fill with ignored files.

## Consequences

- Directories git ignores are never listed, so an unreadable ignored directory no longer stops a scan on any platform, and ignored trees cost no filesystem round trips over `\\wsl.localhost`.
- A Windows runtime indexes WSL repositories with their ignore rules as long as the distribution has git. Each scanned root starts one `wsl.exe` process.
- Repositories on Windows drives need Git for Windows. Before, they were indexed without ignore rules when git was missing; now the ingest fails with the hint to install git. This is a visible change for such installs.
- A `.git` entry git rejects (a broken worktree link, `GIT_CEILING_DIRECTORIES`, dubious ownership on a Windows drive) now fails the scan instead of silently walking it.
- A root inside an ignored directory of a repository still indexes nothing, as before.

## Invalidation conditions

- If Git for Windows lists repositories under `\\wsl.localhost` without a `safe.directory` override, list them with the runtime's git and drop the `wsl.exe` route.
- If ingesting repositories on Windows machines without git becomes a requirement, reopen the in-process listing alternative (git index and ignore files read in Python).
- If `wsl.exe --exec` stops passing its arguments to the program unchanged, revisit how the distribution's git is started.

## Validation

- Linux (WSL2, Python 3.12 test venv): `tests/test_indexing_retrieval.py` covers a walk that never lists an ignored directory (an unreadable one included), the `wsl.exe` command line and `WSL_UTF8` environment for a `\\wsl.localhost` root under a simulated Windows platform, no git run outside a repository, a missing git and a refusing git inside one, and `ingest` exiting 1 with the one-line message. `tests/test_wsl_bridge.py` covers parsing both namespace spellings and rejecting other paths. `test_indexing_retrieval`, `test_wsl_bridge`, `test_audio_ingestion`, `test_locale_encoding`, `test_projects` and `test_fs_browser` pass.
- Linux: scanning tarom-forms, which raised `PermissionError` before, returns 1,045 files.
- Windows (the installed runtime's Python 3.12.8 venv, this checkout on `PYTHONPATH`): the distribution's git listed 1,177 files for `\\wsl.localhost\Ubuntu\…\tarom-forms`, and the scan returned 1,045 (554 under `api`) without error. A temporary folder on `C:` with a `.git` directory failed with the install-git message.
- Not executed: a full `imprint ingest` through the Go CLI on Windows, macOS, Windows with Git for Windows installed, and a distribution without git.
