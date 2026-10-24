---
title: ADR 0023 — Update by installation type
description: The dashboard updates Imprint through the CLI, which installs a release the way the installation was set up.
---

- Status: Accepted; stopping and restarting the dashboard server superseded by [ADR 0048](0048-registered-dashboard-server-and-supervised-restart.md)
- Date: 2026-10-23
- Related: ADR 0002, ADR 0012, ADR 0014

## Context

The dashboard had no way to update Imprint, and the sidebar showed `Imprint unknown` on Windows installs: the API looked for the CLI only on PATH or under a `make all` name, and the Inno installer leaves PATH unchanged by default.

`imprint update` overlaid the release archive onto the install directory. That suits install.sh / install.ps1 trees but not the other distributions:

- The Windows installer copies launcher scripts that the archive omits and records the installed version in the registry. Overwriting a running `imprint-ui.exe` fails midway.
- The macOS `.pkg` installs into the root-owned `/Applications/Imprint.app`.
- A container's `/app` is read-only for the service user, and a recreated container would discard any change.
- Source checkouts with `make all` binaries passed the layout check.
- Nothing restarted the dashboard server, so it kept serving the old code.
- The terminal prompt opened `/dev/tty`, which does not exist on Windows.

## Decision

The CLI remains the single implementation of release resolution and installation. It classifies the running installation by layout: an Inno uninstaller in the install directory, the app bundle path on macOS, or an install script tree. Each type updates its own way, always after verifying the download against `SHA256SUMS`:

- **Install script:** overlay the archive as before, after moving each `bin/` file to `<name>.prev`. This keeps a rollback copy and frees the names of running executables on Windows. Then re-run setup and restart a dashboard server started by `imprint ui start`.
- **Windows installer:** stop the dashboard server, then start the release's `setup.exe` detached with `/VERYSILENT /CLOSEAPPLICATIONS /RELAUNCH=1` and a log in `data/`, and exit. The installer closes Imprint, replaces its files and provisions. Because `/RELAUNCH=1` was passed, it reopens Imprint. A silent update keeps the persisted install profile; a silent first install keeps ADR 0012's CPU/no-LLM defaults.
- **macOS package:** open the `.pkg` in Installer, wait for it, confirm the installed version and restart the dashboard server.
- **Containers and source checkouts:** these are reported as unable to update themselves, with the reason.

The CLI takes the queue lock, so an update never interrupts a running job.

`imprint update --check --json` reports current and latest versions, the update method or the reason none applies. The API caches that report for six hours. `POST /api/update` starts `imprint update -y` outside the server's process group with its output in `data/update.log`. The dashboard shows an update notice in the top bar, explains the steps for the installation type, tails the log and waits until `/api/version` reports the new release.

The API resolves the CLI from its own installation first: `bin/imprint[.exe]`, then a checkout build, then PATH. It caches the version only after a successful call.

## Alternatives

- **The bare overlay for every installation.** It breaks on the installer-managed layouts above.
- **An Electron auto-updater.** It would update the desktop shell but not the CLI, Python package or browser-only installs. Every desktop package wraps the same release tree, so one CLI path covers all of them.
- **Running the update as a queue job.** A job's lifetime is tied to the server the update stops or restarts.
- **Querying GitHub from Python.** That would duplicate the Go release logic.

## Consequences

- Installations older than this release must update once by the old route: the installer, the install script or `imprint update`.
- The dashboard contacts the GitHub releases API at most every six hours while it is open.
- A server not started by `imprint ui start`, such as a foreground `imprint ui`, is not restarted; the log asks for a restart.
- On Windows the app window closes during an update and reopens when provisioning ends.
- On macOS the update needs the administrator prompt.
- Container users are told to pull a new image.

## Validation

- **Go:** unit tests cover version comparison including `git describe` builds, installation classification, the binary backup and flag parsing. `imprint update --check --json` was run against the live releases API.
- **Python:** tests cover the cached check, failure reporting, detached start in its own session, job and duplicate conflicts, and the endpoint's refusals.
- **Browser:** tests cover the notice, the installer handover, the stopped updater with retry and the container explanation.
- **Not yet observed:** the Inno relaunch and profile-preserving provisioning need a native Windows update from one release to the next. The macOS `.pkg` handover needs a native macOS run.
