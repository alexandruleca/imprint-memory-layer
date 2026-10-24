---
title: ADR 0025 — Desktop-owned background service
description: Show startup progress before Python loads and separate hiding Imprint from quitting its local API.
---

- Status: Accepted
- Date: 2026-10-23
- Related: [ADR 0007](0007-verified-runtime-and-desktop-baseline.md), [ADR 0024](0024-enforce-local-service-boundaries.md)
- Desktop-owned services not registering in `imprint_ui.json` is superseded by [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md); the original rationale below is preserved.

## Context

The Windows installer shortcut invoked the console CLI, which started an independent API daemon and waited up to 20 seconds before launching Electron. This bypassed Electron's existing loading screen during normal startup. Electron's standalone API spawn lacked `windowsHide`, ignored diagnostics and resolved the wrong venv location inside installed macOS app bundles. Closing the dashboard hid it, but there was no in-window action explaining how to quit the app and service together.

## Decision

Launch Electron before waiting for the API. Ready Windows installs launch the bundled GUI executable directly; `imprint ui open` on all platforms resolves the source, data and writable Python paths, then opens Electron first. First-run provisioning and explicit repair remain installer/setup responsibilities.

Display a local splash before checking health or importing the backend. Retain it until the dashboard finishes loading. Give readiness up to 60 seconds, report launch/exit/load failures and provide retry, log access and startup cancellation. Use the current splash/error window when reopening during startup, rather than creating a premature dashboard.

Electron starts an API only when one is not already available, using `windowsHide`, redirected logs and a private stdin lifetime pipe. The dedicated `imprint.desktop_server` entry point watches EOF before expensive imports. EOF requests graceful uvicorn and streaming shutdown; a ten-second deadline exits only that API process if shutdown stalls. This also handles Electron crashes. Keep service logs in the resolved data directory at `imprint_ui.log`.

Closing the loaded window hides it and leaves the API running. Reopen from the tray, Dock or another app launch. Explicit **Quit Imprint** in the sidebar or tray closes Electron and its owned API. On desktops without a tray, close minimizes the window so it remains accessible. Cancelling the splash quits and stops startup.

An existing API, including one explicitly started by `imprint ui start`, is reused and left running on Quit. The explicit daemon remains managed by `imprint ui stop/status/log`. Browser-only distributions keep that existing model. Qdrant remains a separate shared daemon for CLI/MCP clients; desktop shutdown never kills a process tree or the shared database. Desktop-owned API processes do not create the CLI daemon's PID file.

## Alternatives

Hiding only the CLI window retains delayed startup feedback and unrelated server lifetime. Installing an OS service adds platform-specific install, permission and upgrade behavior for a per-user dashboard. Killing all descendants can interrupt shared Qdrant clients. Stopping the API whenever the dashboard hides contradicts the requested background behavior. A browser heartbeat cannot distinguish a hidden app from a closed or crashed Electron process reliably.

## Consequences

The shell owns one hidden API without requiring an OS service installation. Existing independently started daemons remain compatible and intentionally outlive the UI. The API can remain active while the dashboard is hidden. Explicit Quit interrupts its active dashboard work; it preserves stored memories and unrelated clients. First-run Python/dependency downloads retain their existing setup UI. A splash can begin only after the OS starts Electron; portable-executable extraction and AppImage startup can still precede it.

## Validation

Linux checks cover the actual API's lifetime pipe, bounded shutdown, shell-first Go launch with resolved paths, and Electron lifecycle tests with window/process doubles. Dashboard validation covers the visible Quit/hide controls. Native CI runs Python, Go and Electron lifecycle regressions across five targets, plus a Windows PowerShell launcher fixture with spaces and an apostrophe in its install path. Added workflow checks are not executed native results. The Windows launcher fixture also passed under native PowerShell 5.1 via WSL interoperability, using a native GUI executable stand-in. Windows/macOS Electron console visibility, tray/Dock behavior and installer upgrades still require native desktop execution; record results in [platform validation](../platform-validation.md).
