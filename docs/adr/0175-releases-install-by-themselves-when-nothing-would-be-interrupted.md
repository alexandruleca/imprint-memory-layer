---
title: ADR 0175 — Releases install by themselves when nothing would be interrupted
description: While the dashboard server runs, it installs a new release on the update channel by itself, on by default, but only through an update method that needs nobody and only when no job, recording, chat reply, Echo conversation, recent agent call or recent keyboard and mouse input would be interrupted.
---

- Status: Accepted
- Date: 2026-11-11
- Amends: [ADR 0023](0023-update-by-installation-type.md) — an update also starts by itself, not only from the user's **Update now** or `imprint update`
- Related: [ADR 0108](0108-installations-opt-in-to-dev-builds-in-settings.md) (the update channel), [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) (the restart after an update), [ADR 0084](0084-setup-frees-the-runtime-before-replacing-its-files.md) (what setup stops), [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) (recordings)

## Context

Updates start only when the user clicks **Update now** or runs `imprint update` (ADR 0023). The user asked for a setting, on by default, so they no longer have to update by hand.

An update is disruptive. It restarts the dashboard server (ADR 0050). On Windows it runs the installer with `/CLOSEAPPLICATIONS /RELAUNCH=1`, which closes the desktop app and opens it again, and setup may stop the runtime's other processes, MCP servers included (ADR 0084). Started at the wrong moment it would end a meeting recording (the dashboard window holds it), a chat reply or an Echo conversation, cut off an agent mid-session, or close the window under the user. A macOS package update opens the Installer app, which needs the user and their password.

## Decision

A setting, `update.auto` (Settings → Updates; env `IMPRINT_UPDATE_AUTO`), on by default, lets the dashboard server install releases by itself. Ten minutes after it starts and then every 30 minutes, it checks for a release on the update channel (the six-hour cache of ADR 0023 still limits how often GitHub is asked) and starts `imprint update -y`, as **Update now** does, only when all of these hold:

- the update method needs nobody: the Windows installer or an archive install, not the macOS package;
- no update is running and no queue job other than background tagging is running (background tagging yields to it, ADR 0067);
- no recording is running, meaning one marked as recording whose audio grew in the last two minutes, since the recorder sends audio every five seconds and a recording whose window closed must not hold updates back for ever;
- no chat reply is being written and no Echo conversation is open;
- no agent called an Imprint tool on this device in the last 15 minutes;
- nobody has used the keyboard or mouse for 10 minutes, where the system says so (Windows `GetLastInputInfo`, macOS `HIDIdleTime`). Elsewhere this check is skipped.

When the updater fails, that release is not tried by itself again for a day; **Update now** still can. The update notice says the release installs by itself, or what it is waiting for. Without a running dashboard server nothing installs by itself.

## Rejected alternatives

- **Install as soon as a release appears.** It would end recordings and conversations and close the desktop app in front of the user, as described in the context.
- **Install only when the user quits or restarts Imprint.** The desktop app usually runs for days, hidden in the tray, so updates would wait just as long. The Windows installer also reopens the app, which a quit must not do.
- **Run the macOS package unattended.** It opens the Installer app and asks for an administrator's password.

## Consequences

- Installations keep up with releases without the user, at a moment they are away.
- On a computer in constant use, or with an agent working through the night, an update waits until the next quiet stretch.
- On Linux the keyboard and mouse check is skipped, so an update can start while the user works, once nothing else is in progress.
- An update can still interrupt work that leaves no trace in these checks, such as a dashboard page left half filled in.
- Installations change behaviour on update: they start updating themselves. Users who want to choose when turn it off.

## Invalidation conditions

- If updates gain a way to apply without restarting the server or closing the desktop app, such as swapping in a new runtime at the next restart, apply them that way and drop the idle conditions.
- If users report updates that interrupted work these checks did not see, add that work to the checks or lengthen the quiet periods.
- If a desktop-environment-neutral way to read user idle time appears on Linux, use it there too.

## Validation

- Windows 11, Python 3.13 (the installed runtime's venv): `tests/test_updates.py` covers the decision (off, up to date, a method that needs the user, waiting with its reason, starting, a failed release waiting a day), each condition in what an update would interrupt, a recording that stopped growing no longer holding updates back, and Windows' idle time; the full suite passes.
- Not run: a real automatic update on any platform, the macOS idle-time reading, and the desktop app reopened by the installer after an automatic start.
