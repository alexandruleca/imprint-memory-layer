---
title: ADR 0091 — WSL homes with a connected client get the imprint command
description: imprint wsl setup puts an imprint command, the Windows bridge's CLI, in the registered home's ~/.local/bin, keeps any other imprint there, and removes it with the home's last registration.
---

- Status: Accepted
- Date: 2026-10-27
- Amends: [ADR 0026](0026-windows-installer-wsl-client-bridge.md) — the bridge's CLI is also the `imprint` command in each registered WSL home, not only `imprint-wsl cli` in each client profile
- Related: [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md), [ADR 0053](0053-connect-clients-without-touching-the-runtime.md)

## Context

The Windows installer connects WSL clients through a private `imprint-wsl` launcher in each client profile ([ADR 0026](0026-windows-installer-wsl-client-bridge.md)). From a WSL shell the CLI was reachable only through that launcher, `"$HOME/.codex/imprint-wsl" cli …` in [installation](../installation.md#clients-inside-wsl); `imprint` itself was not found. The installer's optional *Add Imprint to the user PATH* task exposes `imprint.exe` to WSL through interop, but that process gets a UNC working directory, untranslated Linux operands and none of the bridge's `IMPRINT_WSL_*` context. The request: the `imprint` command the Windows installer installs should also work in WSL.

The Linux installer links `imprint` into `~/.local/bin` (`install.sh`), so a distribution can already hold a Linux installation's `imprint` there, as a symlink. Measured on 2026-10-27 with a Windows Go binary against Ubuntu (WSL2): through `\\wsl.localhost`, `os.ReadFile` of a valid absolute or relative Linux symlink fails with "The system cannot find the path specified" (`os.IsNotExist` is true), while `os.Lstat` succeeds and reports a non-regular file.

## Decision

`imprint wsl setup` writes `~/.local/bin/imprint` in the registered home after the client's profile is registered and its launcher probed. The installer's WSL selections, their replay on repair and update, and dashboard enrollment all run it. The command is the same bridge invocation as `imprint-wsl`, with the bridge's `cli` action fixed, and opens with the same managed-bridge marker. It belongs to the home (distribution and home), not to a client, so a registration for any target installs it, Cursor's learn-only one included.

Another `imprint` at that path stays. That covers anything that is not a regular file (checked with `Lstat` first, because Windows reads a Linux symlink as missing) and any regular file without the marker. Setup warns and the registration still succeeds. A marked command is replaced, so the newest registration's runtime owns it.

`imprint wsl remove`, `disconnect`, `disable` and uninstall remove the command before they drop the record of the home's last registration (`.wsl-registrations.json`). They remove it only when it runs this installation's bridge. A failure keeps the record, so cleanup can be retried.

The commands the bridge's CLI accepts are unchanged. For any other command, or none, it prints the commands it runs and says the rest run from Windows Imprint, exiting 2 without argparse's bridge usage, because people now type it directly.

No shell startup file is edited. Whether `~/.local/bin` is on `PATH` is the distribution's profile's choice; Ubuntu's and Debian's `~/.profile` add it when it exists at login.

## Rejected alternatives

- **Rely on the Windows PATH entry.** The task is opt-in and unchecked by default, and `imprint.exe` started from WSL lacks path translation and WSL context. That gap is why ADR 0026's bridge exists.
- **`/usr/local/bin/imprint` written as root.** It is on every user's `PATH` without profile changes, but it needs `wsl --user root` writes into the distribution, and the script names one user's home. ADR 0026's setup does not elevate or change Linux permissions.
- **Add `~/.local/bin` to `PATH`, or an alias, in shell startup files.** ADR 0026 rejected even sourcing those files during setup. Editing them is more invasive and differs per shell.
- **Make the command the clients' launcher too.** Client registrations would point at a shared file, existing registrations would need migrating, and removal would be per home instead of per client. A client chooses the launcher's action (`mcp`, `hook …`); the command's action is always the CLI.
- **A separate installer choice or WSL target for the command.** WSL targets are the clients table's entries ([ADR 0053](0053-connect-clients-without-touching-the-runtime.md)); a command is not a client.
- **Decide ownership by reading the file.** In the regression run below, a build using only `os.ReadFile` replaced a Linux installation's `imprint` symlink with the bridge command.

## Consequences

- Homes registered before this change get the command when setup runs for them again. The installer's repair and update replay saved selections. A profile enrolled from the dashboard gets it from **Enroll** or `imprint wsl setup`.
- A distribution with no connected client gets no command.
- A shell started before the first registration may not have `~/.local/bin` on `PATH` until the next login. Where the distribution's profile never adds it, run the command by its path.
- In a home with a Linux installation of Imprint, `imprint` stays that installation's. The Windows runtime remains reachable as `imprint-wsl cli …`.
- Setup, installation, updates and desktop commands (`setup`, `update`, `ui`, `server`, …) still run from Windows.
- The shared configuration writer backs up a changed command next to it as `imprint.imprint-<n>.bak`. The content changes only when the Windows runtime moves.
- Only journaled registrations keep the command. A home whose other client is known only from a pre-journal installer selection loses the command when its last journaled registration goes, until the next repair.

## Invalidation conditions

- If the Linux installer stops linking into `~/.local/bin`, or common distributions stop adding it to `PATH`, then the command's location is reopened.
- If ADR 0026's bridge is superseded, for example by a runtime inside the distribution, then this command goes with it.
- If Windows starts resolving Linux symlinks through `\\wsl.localhost`, then the `Lstat` step can become a plain read.
- If the bridge's CLI accepts other commands, then its refusal message lists them; the message is built from the same list.

## Validation

Executed 2026-10-27 on Windows with Ubuntu under WSL2, using disposable homes and Windows data. The installed Windows interpreter was reused read-only. No real client profile was registered.

- Go fixtures (`cmd`): the command's script, idempotent install, a Linux installation's symlink and a user script kept, the command kept while another registration in the home remains (a registration in another distribution with the same home does not count), another installation's command kept, and removal with the last registration, including through `disconnect` cleanup. They passed on Linux and, cross-compiled, natively on Windows. There the symlink case is skipped for lack of the symlink privilege.
- Python (`tests/test_wsl_bridge.py`): the CLI runs Windows Imprint with translated operands, and the refusal lists the supported commands without usage text. The new test fails against the previous bridge.
- `scripts/test-wsl-interop.py` against a cross-compiled `imprint.exe` and the checkout's `imprint/` in Windows temporary storage. Registration installs an executable command. The command approves a Linux project root and refuses `update` with exit 2. A Linux installation's `imprint` symlink survives setup and removal. A home on a Windows drive gets and loses the command. The command stays until the home's last registration is removed. MCP discovery found 19 tools. The smoke's Stop check now uses a transcript with no recorded working directory: since [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md), a cwd no workspace claims is learned rather than refused.
- Regression run: the same setup against a home whose `~/.local/bin/imprint` links to a Linux installation. This change kept the link and warned; a build checking ownership with `os.ReadFile` alone replaced it.

Not run: an Inno Setup compile of the WSL page's changed description, the GUI installer, dashboard enrollment, and distributions other than Ubuntu.
