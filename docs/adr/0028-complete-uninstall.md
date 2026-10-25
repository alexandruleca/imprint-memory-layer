---
title: "ADR 0028 — Complete uninstall"
description: Uninstall stops every process of the installation and removes its runtime and data unless the user keeps the data.
---

- Status: Accepted
- Date: 2026-10-23
- Amends: [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) — data retention and the ask-to-quit step
- Related: [ADR 0025](0025-desktop-owned-background-service.md)
- Restructured: 2026-10-25

## Context

ADR 0027 made uninstall disconnect clients first and keep memories. The maintainer asked for an uninstall that cleans up completely: stop Imprint's processes, then remove local data.

Uninstall stopped only the dashboard daemon and Qdrant. Several kinds of process were left running:

- the desktop shell and the service it owns;
- MCP servers that clients start from the venv;
- running jobs.

They hold the venv and data open, and an uninstall that kept running them left broken processes behind. The Windows uninstaller also ran its cleanup in `InitializeUninstall`, before Inno's own confirmation, so declining that prompt still disconnected clients.

Imprint never starts Ollama or another model server. Its local LLM runs inside its own Python processes, and Ollama is only a provider endpoint the user runs.

## Decision

`imprint uninstall` disconnects clients (ADR 0027), then stops every process of this installation, then removes the runtime and data. Every native uninstaller calls it.

**Which processes belong to the installation.** A process is the installation's if either holds:

- it runs a file from the installation's runtime directories: `bin/`, the venv, the managed Python, or `data/` (Qdrant);
- its command line carries `--project-dir=<installation>`. That is how `imprint ui open` starts the desktop shell, which Windows (portable) and Linux (AppImage) run from a temporary copy.

Process trees are not followed, so a browser or editor Imprint opened is never stopped. Matching a whole argument keeps a neighbouring installation out of reach.

**How they are stopped.** Processes are asked to exit (SIGTERM; on Windows, termination). After ten seconds any survivor is forced. A process that still survives fails the uninstall before anything is deleted.

**Data is removed by default:**

- `imprint uninstall --keep-data` keeps memories and settings.
- The Windows uninstaller asks after its confirmation: Yes deletes, No keeps, Cancel stops. A silent uninstall deletes unless `/KEEPDATA` is passed.
- A full Windows uninstall deletes the remaining install folder once Inno has removed its files.
- The macOS uninstaller asks, then removes the now-empty runtime folder.

**Order on Windows.** Cleanup runs in `CurUninstallStepChanged(usUninstall)`, after the confirmation and before any file is removed, and aborts on failure. `InitializeUninstall` only refuses when the CLI it needs is missing.

## Rejected alternatives

- **Killing process trees under Imprint processes.** This would end the user's browser, which the desktop shell and the browser fallback launch.
- **Matching any command line that mentions the install path.** This would close an editor showing an Imprint log.
- **Asking the desktop shell to quit through a second instance.** That instance exists only to show the window, and on Windows each launch unpacks the portable app again.
- **Keeping memories by default (ADR 0027).** This was rejected at the maintainer's request; keeping is one answer or flag away.
- **Stopping Ollama.** It would end a service Imprint did not start and other applications may use.

## Consequences

- Uninstalling deletes memories unless the user keeps them; export a snapshot first to move them elsewhere.
- Clients lose their running Imprint MCP server immediately rather than at their next restart.
- An Imprint process owned by another user or running elevated cannot be stopped. The uninstall then fails with its name and leaves the installation intact.
- A silent Windows uninstall now deletes data unless `/KEEPDATA` is passed. Scripts that relied on retention must add it.
- On macOS a script install sharing `~/.local/share/imprint` with the app is not deleted by the app's uninstaller.

## Invalidation conditions

- If Imprint starts processes that run from outside its runtime directories and without `--project-dir`, for example a separately installed model server, the ownership rule misses them and must be extended.
- If a supported OS stops exposing process executables or arguments to the user, ownership cannot be established and uninstall must find another way to stop processes.

## Validation

- **Go tests:**
  - ownership rules for installed files, temporary desktop copies, quoted Windows arguments, a neighbouring installation, a browser and an editor;
  - that process listing finds the test process with its executable and arguments, on every CI platform;
  - an end-to-end stop that ends a process run from the installation and a desktop-shell look-alike, while another installation's process keeps running.
- **Windows helper regression:** checks the CLI call with and without `-KeepData`, and the failure path.
- **Release smoke:** the native Windows silent uninstall must stop a process running from the venv, leave no install folder and keep the client cleanup assertions.
- **Not verified here:** the interactive Windows prompt, Inno's abort from `usUninstall` on failure, and the macOS command need native runs.
