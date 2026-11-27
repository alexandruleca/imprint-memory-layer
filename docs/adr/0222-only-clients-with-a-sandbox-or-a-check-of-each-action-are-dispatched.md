---
title: ADR 0222 — Only clients with a sandbox or a check of each action are dispatched
description: The Dispatcher starts a client only through a one-shot mode with an OS sandbox or a check of each action and none of its bypass flags, finds the clients available on each device and WSL distro itself, and ships runners for Claude Code and Codex first.
---

- Status: Proposed
- Date: 2026-11-27
- Related: [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) (the Dispatcher), [ADR 0053](0053-connect-clients-without-touching-the-runtime.md) (the client table), [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (sessions and claim tokens)

## Context

The owner asked that Imprint know which clients are available on the device, "we might have more harnesses installed like opencode etc" (2026-11-27). A Dispatched session runs with nobody to approve its actions.

What each client offers for one-shot, unattended runs, from its `--help` where installed and its official documentation otherwise (2026-11-27):

| Client | One-shot mode | Unattended safety | Bypasses |
| --- | --- | --- | --- |
| Claude Code 2.1.332 | `claude -p` | `--permission-mode auto`: a classifier checks each action | `--permission-mode bypassPermissions`, `--dangerously-skip-permissions` |
| Codex 0.171 | `codex exec` | `--sandbox workspace-write` with `--approve-for-me`: an OS sandbox, and approvals reviewed automatically | `--dangerously-bypass-approvals-and-sandbox`, `--sandbox danger-full-access` |
| Cursor CLI | `agent -p` | actions outside its allow-list refused; `--sandbox enabled` (Landlock on Linux) | `--force`, `--yolo`, `--sandbox disabled` |
| GitHub Copilot CLI | `copilot -p` | requests that need approval refused; `--assisted-approval`; `--sandbox` for shell commands | `--allow-all`, `--yolo`, `--allow-all-tools`, `COPILOT_ALLOW_ALL` |
| OpenCode | `opencode run` | none: no sandbox, and its default policy allows everything but `.env` files | `--auto` |
| Cline CLI | `cline "<prompt>"` | none: approves everything by default, with no allow-list or sandbox | its default |
| OpenClaw | `openclaw agent exec` | none: sandbox off, full execution policy | its default |

Claude Desktop and ChatGPT Desktop have no one-shot command-line mode.

- `imprint clients` reports whether Imprint's entry is in a client's configuration, not whether the client is installed. On Windows it shows ChatGPT Desktop connected because ChatGPT Desktop shares Codex's entry. Each connect handler checks installation in its own way: a binary on PATH, a configuration folder, or an app folder (`cmd/setup*.go`).
- On the owner's device the runtime runs on Windows, and the projects live in a WSL distro. Inside WSL, Claude Code and Codex exist only as the binaries bundled with their VS Code extensions, not on PATH. Windows has its own `claude.exe` 2.1.302 and `codex.exe` 0.166.

## Decision

**Eligible clients.** The Dispatcher starts a client only through a one-shot mode that has an OS sandbox or a check of each action, and never with that client's bypass flags or variables. Under this rule:

- **Eligible:** Claude Code, Codex, Cursor CLI and Copilot CLI.
- **Never started:** OpenCode, Cline and OpenClaw. The Board lists them as installed without a safe unattended mode.
- **Never offered:** desktop and editor-only clients.

There is no full-access option.

**Runners.** Starting a client is one entry in a runner table. An entry says:

- how to find the binary;
- which safety flags it requires, and which flags it refuses;
- how the session's id is set or read;
- how its output is read.

The first version ships two runners. Both take the model and effort from the client's own configuration.

| Runner | Command |
| --- | --- |
| Claude Code | `claude -p --permission-mode auto --session-id <uuid> --output-format stream-json` |
| Codex | `codex exec --sandbox workspace-write --approve-for-me -C <worktree> --json` |

Cursor CLI and Copilot CLI get runners once a device can test them.

**Available.** A client is available on a device, or in one of its WSL distros, when all of these hold:

1. **Its binary is found.** The search goes through the login shell's PATH, then the binary bundled with its VS Code extension, then the `dispatch.<client>.command` setting.
2. **It is new enough.** Its `--help` lists the safety flags its runner passes, so an older version is refused rather than run with less safety.
3. **Imprint is connected to it.** The session needs Imprint to take over its claim.

The check runs when the dashboard server starts, every hour, and when the Board's Dispatcher strip opens. The strip shows each client's version, or why it is unavailable.

**Where it runs.** A project in a WSL distro runs inside that distro: `wsl.exe -d <distro> --cd <folder> --exec <client> …`, with the client found in that distro.

**Which client works a Task.**

1. A Task assigned to a client goes to that client.
2. Any other Task goes to the default client: the first client the person allowed, in the order Claude Code, Codex, then the rest. The person can change it.
3. Every available client is allowed until the person unticks it.

Reviews follow [ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md).

## Rejected alternatives

- **A full-access option for clients without a safe mode.** Declined with the rest of the design (2026-11-27). It would make OpenCode, Cline and OpenClaw usable at the cost of unattended code with the person's full authority.
- **Running those clients inside Imprint's own sandbox** (bubblewrap or similar). That is a project of its own, separate from the Dispatcher.
- **Windows binaries on `\\wsl.localhost\` paths.** Git is slow over that path, and hooks and agents would see Windows paths for a Linux project.
- **`imprint clients` as availability.** It reports configuration only, and shows ChatGPT Desktop connected while it is not installed.
- **Shipping the Cursor CLI and Copilot CLI runners now.** Neither is installed on a device where they can be tested, and they would be untested code running unattended.

## Consequences

- Of the clients Imprint connects to, two can be dispatched at first; the rest show why not.
- The bundled-binary fallback depends on the VS Code extensions' folder layout, which can change with their releases; the setting overrides it.
- A client's update that renames or drops a safety flag makes it unavailable until its runner is updated, rather than running with less safety.

## Invalidation conditions

- If OpenCode, Cline or OpenClaw gains a sandbox or a check of each action in its one-shot mode, give it a runner.
- If a client's safe mode lets through an action that damages files outside its worktree, stop dispatching that client until a new record decides.
- If Claude Code or Codex is installed on PATH inside WSL on the owner's devices, drop the bundled-binary fallback.

## Validation

Not implemented. On 2026-11-27, `--help` was read for Claude Code 2.1.302 and 2.1.332 and for Codex 0.166.0 and 0.171.0-alpha.13.2; the other clients' rows come from their official documentation. The installation checks were read in `cmd/setup*.go` and `cmd/cleanup.go`.
