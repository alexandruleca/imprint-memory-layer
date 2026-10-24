---
title: ADR 0046 — Visible first-time Echo setup
description: Explicit dashboard provisioning with real byte progress, persistent status and owned cancellation.
---

- Status: Accepted (release toolchain policy superseded by [0047](0047-bundled-private-voice-engine.md))
- Date: 2026-10-24
- Related: [0044](0044-native-desktop-voice-platforms.md), [0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md)

## Context

Echo previously required a manual compiler/download command and showed only a
small setup-guide link. The user requested visible first-download UX: people
must understand what is happening before waiting for large local assets.

## Decision

Provide an explicit **Download & set up Echo** action on Voice and in the floating
companion. Show the approximately 1.07 GB model download, saved-file reuse, extra
engine download and temporary disk needs before starting. Check CMake, the
platform C++ toolchain and 4 GB free space before the large download. Explain
missing prerequisites with a retry and platform instructions; do not install
system tools automatically.

Report actual aggregate model bytes, including fully verified cached files.
Separate download/verification from engine preparation/build/final checks.
Preparation has an indeterminate indicator, without an invented percentage or
completion time. Keep a top-bar status link visible across dashboard navigation.
Never start voice or enable the microphone upon setup completion.

Also disclose the separate model needed by the default same-Chat reasoning
route. Forward its existing install/download/load events into a visible voice
preparation panel with actual byte progress, excluding raw package logs, paths
and repository metadata. First-time chat provisioning gets a bounded 30-minute
setup budget; return to the ordinary two-minute reply budget on its first token.
Otherwise a valid initial model download could exhaust the reply timeout before
it finishes. Preserve ADR 0035's policy of completing chat provisioning safely
after voice cancellation; clearly disclose this and reject stale progress after
interruption. The LFM-only route needs no additional chat model.

Use a dedicated managed setup child and a bounded JSON progress protocol. The
browser may start/cancel the fixed installer, but cannot supply cache paths,
URLs, executables or commands. Mutation endpoints require the exact local
dashboard Origin and loopback peer/Host. Only one setup is active per API, with
an additional per-cache process lock. Voice and setup cannot own the cache at
the same time in that API. Pinned sizes, checksums and source/build verification
remain mandatory. Raw compiler output and private paths do not reach the UI.

The existing `voice.runtime_dir` setting/environment override remains authoritative.
When empty, use `voice/` under Imprint's data directory, without rewriting saved
configuration or bundling caches into distributions. Setup writes only this
dedicated cache. Models/runtime are still optional; opening Voice does not
download anything. The default-cache behavior extends ADR 0044's manual-only
provisioning policy.

A private owner pipe stops the setup process and its compiler descendants on
cancel or API exit. POSIX uses the setup child's own process group; Windows
terminates the subtree rooted at the setup child itself. Cancellation removes
only that job's partial files. Verified files survive; an unfinished file starts
again on retry. A later exclusive setup cleans managed partials left by abrupt
API termination. Setup continues across page/window navigation while the API
remains running. Closing the companion ends voice; it does not cancel a download
started explicitly by the user.

## Alternatives

- Keep the CLI and add a cosmetic spinner: gives no real byte progress or usable
  first-time action.
- Start downloads when the page opens: spends bandwidth/storage without a
  deliberate user action.
- Run builds in an API thread: makes cancellation and abrupt-owner cleanup of
  compiler subprocesses unreliable.
- Bundle native workers or install toolchains automatically: a separate release
  and system-installer policy change; not required for this optional pilot.

## Consequences and validation

The initial setup still needs native build tools and can take several minutes.
There is no partial-file HTTP range resume, compiler progress percentage or
cross-API voice-session lock. Native platform execution is required for process
tree cancellation; workflow definitions alone do not establish it.

Tests cover exact origins, bounded/no-argument mutations, setup/voice exclusion,
real progress bytes and cached reuse, prerequisite failure before downloads,
owned-partial cleanup, late ready events after cancellation, and native owner
EOF with an actual compiler-like descendant. Browser scenarios cover explicit
first-run action, size/space disclosure, navigation persistence, indeterminate
preparation, completion without mic access, blocked/retry/cancel and narrow
transparent companion layout. The native desktop smoke also starts the actual
managed setup child against its previously verified native cache. Executed
results and remaining limits belong in platform validation.
