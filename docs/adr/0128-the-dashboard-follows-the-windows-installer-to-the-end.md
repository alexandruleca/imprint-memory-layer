---
title: ADR 0128 — The dashboard follows the Windows installer to the end
description: During a dashboard update on Windows, the server reports the installer's progress from its log; a failed installer stops the wait, a finished one whose server restart did not happen gets the server restarted, and a dashboard that still serves the old version says to reopen Imprint.
---

- Status: Accepted
- Date: 2026-11-04
- Amends: [ADR 0023](0023-update-by-installation-type.md) — what the dashboard waits for after the Windows installer takes over
- Related: [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md), [ADR 0084](0084-setup-frees-the-runtime-before-replacing-its-files.md)

## Context

The user reported that on Windows the update dialog stays open after an update, without reloading into the new version, until it is closed by hand.

ADR 0023's dialog waits for exactly one thing: `/api/version` reporting the new release. On Windows, `imprint update` exits once it has started the Inno Setup installer. From then on the dashboard learns nothing:

- A successful exit means the updater handed over, so the dialog keeps waiting.
- The installer runs `imprint-setup.ps1`, which provisions the release and restarts a running dashboard server with `imprint ui restart`. If that restart does not reach the server, the old server keeps answering with the old version. The dialog then waits forever.
- If the installer fails, the dialog also waits forever. In silent mode with `/SUPPRESSMSGBOXES`, Inno Setup answers "Abort" when a file is in use.

ADR 0023 also expected the app window to close during the update and reopen afterwards. The report shows the window stayed open on the user's machine, so a stuck dialog was visible.

The installer's own log is available. `imprint update` already passes `/LOG=data/update-setup.log`. Inno Setup logs "Installation process succeeded." when its install step ends, runs the `[Run]` entries, which include provisioning and the server restart, and ends the log with "Log closed." (an Inno Setup forum thread on installers hanging at the end shows that order).

Which of these happened on the user's machine is not known yet: their `update.log` and `update-setup.log` have not been read.

## Decision

- `POST /api/update` deletes a previous `update-setup.log` before starting the updater.
- Once the updater has exited successfully, `GET /api/update` reports `installer`, read from that log:
  - `running` until the log is closed;
  - `succeeded` if the closed log contains the success line;
  - `failed` if the log closed without it. `failed` is then also true.

  The log's tail is appended to the updater's.
- The dialog:
  - stops with the log when `failed` is true, the installer included;
  - when the installer has succeeded but the old version still serves, asks the server to restart once (`POST /api/restart`, ADR 0050). The restarted server serves the installed code, and the page reloads when it reports the new version;
  - if the new version is still not served 60 s after that request, tells the user that the installer finished but the dashboard still runs the old version, and to close and reopen Imprint.

The archive and macOS package paths are unchanged: their updater restarts the server itself and reports its own failure.

## Rejected alternatives

- **Reloading the page whenever the server restarts.** A server restarted into the old release would load an old page that offers the same update again, hiding whether the installer worked.
- **Having `imprint update` wait for the installer on Windows.** The installer replaces `bin\imprint.exe`, the updater's own binary, so the updater must exit first (ADR 0023).

## Consequences

- An update can no longer leave the dialog spinning forever on Windows: it ends in a reload, a failure with the installer's log, or a message to reopen Imprint.
- The fix reaches users from the update after the one that installs it, because the dialog doing the waiting belongs to the version being replaced.
- The restart fallback hides a failed `imprint ui restart` in the setup script. The installer log still shows that failure.
- Failure means "the log closed without the success line", not a specific error. A log Inno Setup never closes, for example when the installer is killed, keeps the dialog waiting as before.

## Invalidation conditions

- If the user's logs show the window stays open because the installer cannot close the desktop app, fix the close (or `/CLOSEAPPLICATIONS`) as well, in its own record.
- If Inno Setup changes its log lines, match the new ones.
- If the Windows update moves to a process that reports completion itself, drop the log reading.

## Validation

- `tests/test_updates.py`: a new update removes the old installer log; no `installer` field before the installer writes; `running` after the success line until the log closes, then `succeeded`; a log that closes without success is `failed`; an updater that failed ignores any installer log.
- `imprint/ui/tests/update.spec.ts`: no restart while the installer runs; one restart after it succeeds, then a reload into the new version; a failed installer stops the wait with its log and offers Try again; with fake timers, a dashboard that stays on the old version 60 s after the restart says to reopen Imprint.
- Not verified on Windows: the report's root cause, real Inno Setup logs and the restart fallback on hardware.
