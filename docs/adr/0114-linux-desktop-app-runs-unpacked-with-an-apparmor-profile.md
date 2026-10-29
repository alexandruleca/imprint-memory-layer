---
title: ADR 0114 — Linux desktop app runs unpacked with an AppArmor profile
description: Setup unpacks the Linux AppImage and, where AppArmor restricts user namespaces, installs a profile with sudo after asking; ui open falls back to the browser when the app dies at startup.
---

- Status: Accepted
- Date: 2026-10-29
- Related: [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) (Electron baseline)

## Context

On Ubuntu 24.04.5 (bedrock), `imprint ui open` printed "Opened", but no window appeared. Two separate faults stopped the packaged AppImage `bin/imprint-ui`:

- It exited with `dlopen(): error loading libfuse.so.2`. Ubuntu 24.04 ships FUSE 3 only.
- Unpacked, it aborted: `The SUID sandbox helper binary was found, but is not configured correctly`. `kernel.apparmor_restrict_unprivileged_userns` is 1, and `unshare -Ur` was denied, so Chromium's namespace sandbox has no user namespaces. Ubuntu grants them to apps through AppArmor profiles. VS Code, Slack and Discord ship such profiles in `/etc/apparmor.d`.

`launchElectron` reported success as soon as the process started, so neither failure reached the user.

## Decision

- On Linux, setup unpacks `bin/imprint-ui` with `--appimage-extract`, which needs no FUSE, into `bin/imprint-ui.d`. It unpacks again only when the AppImage's size or modification time changes.
- Where AppArmor restricts user namespaces, setup writes an unconfined profile that grants `userns` to the unpacked binary, like Ubuntu's own profile for VS Code. The profile is per user: `/etc/apparmor.d/imprint-ui-<uid>`.
  - In a terminal, setup asks before installing it with `sudo`.
  - Without a terminal, or when the user declines, setup prints the exact commands.
- `ui open` prefers the unpacked app. It treats an app that exits with an error within three seconds as failed: it shows the app's last log lines from `data/desktop-app.log` and opens the browser.

## Rejected alternatives

The owner chose among three options after the faults above were found:

- **`--no-sandbox` when user namespaces are blocked.** It needs no root, but it turns off Chromium's renderer sandbox.
- **Browser only.** It loses the desktop app on every Ubuntu 24.04 machine.
- **Run the AppImage through `APPIMAGE_EXTRACT_AND_RUN`.** It avoids FUSE but extracts about 125 MB on every launch, and it does nothing for the sandbox.

## Consequences

- Linux installs keep a second, unpacked copy of the desktop app, about 300 MB.
- The AppArmor profile needs root once per install path. Without it, the desktop app does not start on Ubuntu 23.10 and later, and `ui open` uses the browser.
- A user's `ui open` now reports a startup failure instead of claiming success.

## Invalidation conditions

- If the Linux desktop app ships as a .deb that installs its own AppArmor profile, then setup's unpacking and profile become unnecessary.
- If Ubuntu stops restricting unprivileged user namespaces, then the profile step is skipped automatically.
- If Electron's AppImage stops needing FUSE 2, then unpacking can be reconsidered.

## Validation

- Executed on bedrock, 2026-10-29:
  - `prepareDesktopApp` unpacked the released AppImage into a temporary install and staged a profile naming the unpacked binary.
  - Without the profile, `startDesktopApp` reported the sandbox abort from the log and returned failure.
  - `go vet` for Linux, Windows and macOS.
- Not executed: loading the profile with `apparmor_parser` and starting the app sandboxed, because that needs the owner's sudo.
