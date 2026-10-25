---
title: ADR 0053 — Connect and disconnect clients without touching the runtime
description: imprint connect and disconnect register and unregister clients through one client table, so the dashboard's Commands page can rehook and unhook every client; enable and disable keep their lifecycle meaning.
---

# 0053 — Connect and disconnect clients without touching the runtime

- Status: Accepted
- Date: 2026-10-25
- Related: [ADR 0026](0026-windows-installer-wsl-client-bridge.md), [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md), [ADR 0032](0032-one-onnx-runtime-per-environment.md), [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md), [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md), [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md)

## Context

The maintainer asked for the dashboard's Commands page to offer every command, enable, disable and setup included, so every client can be rehooked or unhooked from the dashboard. A Commands page job is the `imprint` CLI run as a subprocess of the dashboard server, under the queue (docs/queue.md). This is what each of those commands would do there.

- **`imprint setup [target]`** runs `setupBackend` before registering the client. That step creates the venv if needed, syncs the ONNX Runtime requirements and, when `with_llm` is on, installs the local LLM overlay.
  - On the gpu and auto profiles, the overlay installs the Vulkan build of `llama-cpp-python` on every run. Where llama.cpp finds no GPU, it then installs the CPU build again (ADR 0049).
  - The dashboard runs from this venv and loads that binding for chat, tagging and Echo. On Windows a loaded DLL cannot be replaced, so the swap fails partway (ADR 0032, ADR 0035).
  - Changing the profile also uninstalls the ONNX Runtime the dashboard has loaded.
- **`imprint enable [target]`** is setup plus a Qdrant warm-up, so it has the same problem.
- **`imprint disable`** disconnects every client. It then stops the registered dashboard server, which is the one running the job, and stops Qdrant, the store the dashboard reads.
- **The target lists were copied by hand.** The `setup` error, the usage text and enable's error each had their own copy, and enable's usage text was missing claude-desktop, chatgpt-desktop and openclaw. The Cursor WSL registration (ADR 0052) added its own list of WSL targets.
- **Queued jobs inherited the server's standard input.** Nothing guaranteed that a prompt would fail instead of waiting.

A first version of this change gave enable and disable the client-only meaning. The maintainer kept them as lifecycle commands instead: enable provisions, connects and starts; disable disconnects every client and stops the services. The client-only meaning gets its own commands.

## Decision

**One client table.** `cmd/dispatch.go` lists every client with:

- its name, label and aliases;
- its connect handler and its registrations (the files the handler writes);
- for the clients a WSL profile can be registered for, its directory in that profile (`wslHome`).

Setup, enable, connect, disable, disconnect and `imprint wsl` all resolve their targets through it. The usage text, the errors, the WSL target list and `wslClientDir` are generated from it.

**`imprint connect [client]`** (default all) registers the installed runtime with clients: MCP server, hooks and instructions, exactly as setup writes them. It installs nothing and starts nothing. When the runtime is missing it fails and says to run `imprint setup` first.

**`imprint disconnect [client]`** (default all) removes only the chosen clients' registrations, in local profiles and in the WSL profiles recorded for them. Installer selections for other clients stay, so repair still reconnects them. It stops nothing.

**`imprint clients [--json]`** lists every client and whether this runtime is registered with it.

**`imprint enable` and `imprint disable` behave as before.** Internally they now go through the same table: enable through `Setup`, disable through `disconnectClients` over every client, with uninstall still calling disable. Enable's usage text and error now list every client.

**The dashboard reads its commands from the API.**

- `imprint/commands.py` holds the Commands page catalog: each command's label, description, group and fields, and the confirmation text for any command that removes something.
- `GET /api/commands` serves the catalog together with the output of `imprint clients --json`.
- `POST /api/commands/{command}` accepts the catalog's commands plus the ones the workspace dialogs queue (wipe, migrate, workspace).
- `queue.build_command_args` maps the fields to CLI arguments, and a test checks that every catalog field reaches the CLI.
- The page's own code only adds icons. The page gains:
  - **Connect clients** and **Disconnect clients**, which run `connect` and `disconnect`. Each targets one client or all of them and shows each client's connection state. Disconnect asks first.
  - **Diagnose**, which runs `doctor`.
  - The desktop-export switch for learn.

**Queued jobs have no standard input.** The dispatcher gives each job the null device, so a prompt reads end-of-file instead of waiting.

**Commands the page does not offer, and why:**

- `setup`, `enable`, `bootstrap` and `profile` install into the runtime the dashboard runs from.
- `disable`, `ui`, `server` and `uninstall` stop or remove the dashboard itself.
- `wsl` needs a Windows profile choice.
- Workspaces, purge, move, sync pairing and updates have their own dashboard flows.

## Alternatives

- **Give enable and disable the client-only meaning** (this change's first version). The dashboard would need no new commands. The maintainer rejected it: enable and disable are the commands that turn Imprint on and off, services included.
- **Run setup, enable or disable from the dashboard as they are.** Setup and enable would install dependencies under the running server; on Windows a failed swap leaves the binding half installed. Disable would stop the dashboard that queued it.
- **Have disable skip stopping the dashboard when the dashboard runs it** (for example by detecting `IMPRINT_QUEUE_FOREGROUND`). One command would then mean two things depending on who started it.
- **Provision from the dashboard by restarting through its supervisor** (ADR 0050). This could later give the dashboard a real setup. It needs provisioning outside the server process, and the ADR 0049 swap to stop reinstalling a build that is already right. Rehooking needs neither.
- **Keep the target lists in TypeScript, in the API or in `cmd/wsl.go`.** They drift, as enable's usage text already had.
- **Report clients through `imprint status --json`.** Status starts Python for Qdrant statistics on every call, and it checks only Claude Code.
- **Leave WSL registrations alone when disconnecting one client.** "Disconnect Claude Code" would then leave a WSL Claude Code wired to this runtime, and Cursor's WSL conversations still learned.

## Consequences

- **The dashboard can rehook and unhook safely.** Connect and disconnect run from the dashboard without restarting it or changing its runtime. An end-to-end run through the real dispatcher installed nothing and stopped nothing.
- **Users have two ways to connect clients.** Enable provisions, connects and starts services. Connect only registers. The same holds for disable and disconnect.
- **ChatGPT Desktop shares Codex's registry.** Disconnecting either one disconnects both, except from WSL, where ChatGPT Desktop registers nothing.
- **Connect does not restore WSL registrations.** It writes local profiles only, so a WSL registration that disconnect removed comes back only with `imprint wsl setup`. The dashboard does not offer WSL registrations yet.
- **The page depends on the CLI.** When it cannot run `imprint clients`, for example with a stale `bin/` in a checkout, the Commands page shows the error with a Retry button rather than a partial list. Queued jobs run the same CLI, so they would fail too.
- **The WSL target order changed.** It now follows the client table: claude-code, cursor, codex.
- **The setup swap remains.** Setup still reinstalls the LLM binding on every run when there is no GPU offload (ADR 0049); this change does not touch that.

## Validation

All of the following ran on Linux/WSL2.

**Go.** `go vet` and the full Go suite pass, including the existing cleanup, WSL journal and Cursor WSL tests with the target filter. The new tests check that:

- the client table resolves names, aliases and all, and the usage text names every client;
- the WSL targets, their choices and `wslClientDir` come from `wslHome`;
- `connectClients` runs only the target's handlers, with the runtime it is given;
- a registry two clients share is listed once;
- in a disposable home, connecting Cursor registers only Cursor, and disconnecting Cursor removes its registration, hooks and rule while Codex stays connected;
- disconnecting Codex keeps unrelated settings;
- disconnecting Claude Code keeps the Codex and Cursor WSL selections.

**Python.** The full suite passes. New tests check that:

- every catalog field changes the CLI arguments;
- connect, disconnect, doctor and learn reach the CLI with their arguments;
- only disconnect asks first;
- the allow-list is the catalog plus the workspace flows, and setup, enable, disable, bootstrap, profile, ui, server, uninstall, update and wsl return 404;
- `GET /api/commands` serves the catalog with the CLI's clients, and a failing CLI returns 502 with its output;
- a queued job that reads stdin gets end-of-file while the server's own stdin stays open (the test times out without the fix).

**Browser.** `tests/commands.spec.ts` covers:

- listing the catalog by group;
- connecting a chosen client and seeing its new state after the catalog reloads;
- the confirmation: cancelling queues nothing, confirming queues disconnect for the chosen client;
- switch and fixed arguments;
- a catalog that fails to load, then loads on Retry.

The root-approval and log-follow specs now serve the catalog and pass.

**End to end.** The real CLI built from this tree ran under the real queue dispatcher, in a disposable home and runtime layout. `connect cursor` registered Cursor without running uv or pip, `imprint clients` reported it connected, and `disconnect cursor` removed it.

**Not executed:**

- Windows and macOS native runners;
- the packaged desktop app;
- real clients loading the changed configuration;
- WSL registration removal against a real distribution.
