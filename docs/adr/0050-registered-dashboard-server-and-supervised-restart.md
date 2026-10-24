---
title: ADR 0050 — Registered dashboard server and supervised restart
description: The serving dashboard registers itself, and every launcher starts it again when it exits for a restart, so updates replace it whoever started it.
---

- Status: Accepted
- Date: 2026-10-24
- Related: [ADR 0023](0023-update-by-installation-type.md) (the update methods' server stop/restart is superseded here), [ADR 0025](0025-desktop-owned-background-service.md) (desktop-owned servers now register themselves)

## Context

A Windows installer update from v0.20.0 to v0.21.0, started from the dashboard, ended with the dialog reporting that the updater had stopped and offering **Try again**. Try again reported "Already on v0.21.0". Closing and reopening Imprint showed the new version. The logs in the install's `data/` directory show why:

- `imprint update` (windows-setup) called `uiStop` before starting the installer. `uiStop` only knew a server recorded in `data/imprint_ui.json`, which only `imprint ui start` wrote. The desktop app starts its own service (`imprint.desktop_server`), and ADR 0025 decided it does not write that file. Nothing was stopped.
- The installer's Restart Manager found one application using its files ("Imprint memory dashboard") and shut it down. That was the portable executable's stub in `bin\`, which waits for the Electron app it extracted to a temporary directory. The app itself is not an installed file and kept running. So did its service (pid 40992 in `imprint_ui.log`), until the user quit the app about a minute after the installer finished.
- The installer's relaunch started a second app instance, which yields to the running one through the single-instance lock and only brings its window forward.
- `/api/version` resolves the version once per server process, so the old server kept reporting v0.20.0. The update dialog treated the updater's exit as the end of the update. The windows-setup updater exits as soon as the installer starts, so the dialog said it had stopped.

The flaw is general. Two launchers (the desktop app and `imprint ui`) start the same server, and only one registered it. The archive and macOS package methods restarted only a server started by `imprint ui start`. The desktop shell also survives an update in practice with every desktop package: the Windows portable executable and the Linux AppImage run from a temporary copy, and macOS Installer does not quit running apps. So an update must replace the server underneath a running shell.

## Decision

**The server registers itself.** `imprint.api.DashboardServer` serves the dashboard for every launcher (`python -m imprint.api` and `imprint.desktop_server`). Once it is listening it writes `data/imprint_ui.json` with its pid and port. On shutdown it removes the file if the file still names it. The CLI no longer writes the file. `imprint ui stop`, `status` and `restart`, the already-running check in `imprint ui start`, CLI commands that enqueue through the API, and `imprint update` read it. So they act on the running server whoever started it.

**A restart is the server exiting for its owner to start it again.** `POST /api/restart` makes the server finish in-flight requests, shut down and exit with status 75 (`EX_TEMPFAIL`). Whoever started the server starts it again when it exits with that status:

- The desktop app's `DesktopService` spawns the service again. Any other unexpected exit is still reported on the startup screen.
- `imprint ui` (foreground) supervises its server the same way. `imprint ui start` now leaves `imprint ui serve` running detached, which supervises the server and writes to the UI log.

Ownership does not change: a desktop-owned service stays desktop-owned and Quit still stops it, and a daemon stays a daemon. A server started without one of these launchers (for example `uvicorn imprint.api:app`, or the container's `python -m imprint.api`) just stops; `uvicorn imprint.api:app` answers 409 because it has no `DashboardServer`.

**`imprint ui restart`** restarts the registered server and waits up to 60 seconds for the replacement to register and answer. When no server runs, it starts nothing and exits successfully. Before this change it ran stop then start.

**Every update method restarts the server after installing.** Archive and macOS package updates run the installed binary's `imprint ui restart`. The Windows installer's provisioning script, `imprint-setup.ps1`, ends with `imprint ui restart`. That covers silent updates, interactive upgrades over a running Imprint and **Repair**. A failed restart is logged and does not fail setup. The windows-setup updater no longer stops the server before starting the installer, and `/RELAUNCH=1` still reopens or focuses Imprint.

**The dashboard reloads into the new version.** `/api/update` reports `failed` when the updater it started exited with an error. Only then does the dialog say the update failed and offer **Try again**. A successful exit hands over to the installer or the server restart. The dialog polls until `/api/version` reports the target release, then reloads the page.

**Windows background programs get a console without a window.** `imprint ui start`'s supervisor and the dashboard's updater are started with `CREATE_NO_WINDOW` instead of `DETACHED_PROCESS`. Console programs they start (Python under `imprint ui serve`; setup and `imprint ui restart` under the updater) inherit that console instead of each opening a window.

## Alternatives

- **The desktop app writes the registration for its child.** That makes two writers for one fact and still leaves the app's service without a restart path. The server is the one thing every launcher shares.
- **Stop the registered server and start a daemon in its place** (the old `imprint ui restart`). If the desktop app also restarted its service, both would race to bind the port, and which one served afterwards would depend on timing. If it did not, the server would change owner after every update, and Quit would leave a daemon behind.
- **The desktop app restarts its service after any unexpected exit.** `imprint ui stop` could then never stop it, and a restart would still race a CLI-started replacement.
- **The server re-executes itself.** `execv` keeps the pid and the lifetime pipe on POSIX, but Windows has no `exec`. The parent sees an exit there anyway, and a replacement would need hand-built handle and console inheritance.
- **Close the desktop app for the Windows installer** (ask it to quit, or install Electron unpacked so Restart Manager closes it). This would fix only one method: archive overlays and macOS packages also leave the shell running. It changes the release packaging, and it could be adopted later to refresh the shell itself.
- **Stop the server before the Windows installer runs.** The desktop app would show a startup error for the length of the installation, and starting the service afterwards would need a separate path.
- **Reload every open page when the server's version changes.** That would need a version watcher in every page; this change keeps it to the update dialog.

## Consequences

- An update by any method replaces the running dashboard server with the installed code without changing who owns it. The page that started the update reloads itself into the new version. The false "stopped" state and **Try again** after a Windows installer handover are gone.
- Stopping or restarting waits at most 5 seconds for in-flight requests (`GRACEFUL_SHUTDOWN_SECONDS`), then closes the rest. Unbounded, a restart on Windows hung about once in 40 attempts after the v0.22.0 release: when a client resets its socket, CPython 3.12's proactor transport raises in `_call_connection_lost` before notifying uvicorn, so uvicorn waits for a connection that is already gone.
- The desktop shell (Electron's main process and preload) keeps running the code it started with until the app is quit and reopened. The dashboard pages come from the server and are current.
- `imprint ui stop` also stops a desktop-owned service; the app then shows "Local service stopped" with **Try again**. `imprint ui status` reports it.
- A daemon started by `imprint ui start` is now two processes: the Go supervisor and the Python server. `imprint ui stop` stops the server, and the supervisor exits with it.
- When the queue lock is busy, CLI commands that enqueue through the API now also reach a desktop-owned server.
- Releases before this one neither register their server nor implement `/api/restart`, and their updater does not run the new restart. After the first update to this release, reopen Imprint (or restart an `imprint ui start` server) once, as ADR 0023's transition required. No fallback to the old stop-and-start is kept.
- Provisioning on Windows runs while the old server runs, as it already did in practice. If a release changes dependencies, files loaded by running Imprint processes (the dashboard and MCP servers) could still block their replacement. This change does not address that.
- Other open dashboard pages (another browser tab, the Echo companion) reconnect to the restarted server but keep their old page code until they are reloaded.

## Validation

Executed on Linux/WSL2:

- **Python:** the real desktop service registers its pid and port, removes the registration when its pipe closes, and exits with 75 after `POST /api/restart`. An app without a `DashboardServer` refuses with 409. The updater status distinguishes a handover from a failure, and the launcher configuration test covers `DashboardServer`. The full suite passes (335 tests).
- **Go:** the supervisor restarts on 75 only and ends on a signal. `restartRegisteredUI` waits for the replacement, starts nothing without a running server, and reports a server that cannot restart or does not come back. The full suite passes.
- **Electron:** `DesktopService` respawns on 75, quit ends the replacement, 75 during quit does not respawn, and a failed respawn is reported. 22 tests pass.
- **Browser:** the update dialog waits through the installer handover without offering Try again, then reloads when the new version serves, and offers Try again after a failure. The full Playwright suite passes (59 tests).
- **End to end, with the real CLI and Python server:** `imprint ui start`, `restart`, `status` and `stop` kept the same supervisor while the server pid changed, and left no process or registration behind. The real `DesktopService` from `ui-electron/service.js` was driven with the real CLI: `imprint ui status` saw its service, `imprint ui restart` replaced it without a reported failure, and `imprint ui stop` reached the app as "Local service stopped".

Executed natively on Windows through WSL interop:

- `scripts/test-windows-setup.ps1` passes under Windows PowerShell 5.1. Setup ends with `ui restart`, and a failing restart is logged without failing setup. Run against the previous `imprint-setup.ps1`, the test fails.
- The cross-compiled Go tests pass on Windows: supervisor restart, restart waiting and refusal, and a console child of a `detachAttrs` process has no visible window. The DETACHED_PROCESS counterpart, which would open a window on the desktop, was not run.

Not executed: a real Inno Setup update from one release to the next, an interactive installer upgrade, the macOS package handover, and the packaged Electron app on Windows, macOS or Linux, including the startup screen after `imprint ui stop`. Record native results in [platform validation](../platform-validation.md).
