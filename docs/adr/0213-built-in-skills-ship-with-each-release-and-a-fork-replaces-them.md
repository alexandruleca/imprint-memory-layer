---
title: ADR 0213 — Built-in skills ship with each release, and a fork replaces them
description: Imprint ships its own skills read-only with each release and never syncs them; the user edits one by forking it into an ordinary skill of the same name that replaces the built-in on every device, and the Skills page says when a release changes a forked built-in.
---

- Status: Accepted
- Date: 2026-11-27
- Scope: Only `imprint-tasks` ships; the `imprint` skill comes with [ADR 0214](0214-the-rules-block-shrinks-to-a-stub-once-agents-keep-searching-first.md).
- Related: [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) (the registry), [ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md) (client folders), [ADR 0170](0170-skills-and-personas-reach-every-mcp-client-through-imprints-server.md) (skills through MCP), [ADR 0214](0214-the-rules-block-shrinks-to-a-stub-once-agents-keep-searching-first.md) (the `imprint` skill)

## Context

The owner asked for "skills for this that come built into imprint for using the task system", proposed that "the imprint functionality be a built in skill that gets injected with other skills" (2026-11-27), and decided that "you should be able to edit them by creating a fork that overwrites the system built in".

- The registry keys items by a random id; one item that is not removed holds each kind and name ([`registry.py`](../../imprint/registry.py), `_name_free`). Its origins are `user`, `adopted` and `client` (`ORIGINS`).
- Items reach the clients' folders through [`registry_files.py`](../../imprint/registry_files.py), which records what it wrote in `.imprint-managed.json` and leaves alone what it did not write.
- No skill ships with Imprint today. Releases copy the repository tree minus `.releaseignore` (`scripts/package-platform.sh`), and bundled files are found by path next to the code (for example `requirements/` in [`downloads.py`](../../imprint/downloads.py)).

## Decision

**Shipped with the release.** Built-in skills live in the repository next to the code and ship with each release. The registry lists them with the origin `builtin`, read-only, and writes them into the clients' folders and MCP handshakes like the user's items. They are not synced: each device has its own release's version, which describes that release's tools. The first are `imprint` (how to use Imprint's memory) and `imprint-tasks` (how to use the Board).

**Forks.** Forking a built-in creates an ordinary user skill with the same name and the built-in's files. While the fork is active it holds the name, so clients get the fork and not the built-in, and it syncs to paired devices like the user's other skills. A user skill that already has a built-in's name is treated as its fork.

**Release changes.** When a release changes a built-in that the user has forked, the Skills page says so, shows the difference, and offers **Reset to built-in**, which removes the fork. Nothing is merged automatically.

An edit made to a built-in in a client's folder becomes its fork, as an edit to any item comes back to the registry ([ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md)).

**Disabling.** A built-in can be disabled without forking it. The choice is recorded as a registry record of its own, which syncs, so it holds on every device.

## Rejected alternatives

- **Editing built-ins in place.** Raised by Claude as a question on 2026-11-27; the owner chose forks. An edited built-in would be overwritten by the next release, or would stop receiving the release's changes.
- **Syncing built-ins.** Devices on different releases would exchange versions describing tools the other does not have.
- **Merging a release's changes into a fork.** Weighed in the design discussion; a merge can silently change instructions the user wrote, so the user sees the difference and chooses.

## Consequences

- Built-in skills reach every connected client on install and update, with no setup of their own.
- The names of built-ins are taken. A user's existing skill of the same name becomes the fork, and keeps working as before.
- A fork stops following the release until the user resets it.
- The registry gains an origin and a disabled marker for items it does not store.

## Invalidation conditions

- If clients read skills from MCP servers directly, serve built-ins that way instead of writing them into the clients' folders.
- If forks regularly fall behind built-ins in ways that break the tools they describe (a fork calls a tool or argument the release no longer has), merge non-conflicting changes in a new record.

## Validation

- `tests/test_builtin_skills.py` (21 tests): built-ins listed read-only with an id the same on every device and written into the clients' folders; edits and removal refused with the way to fork; fork and reset through the registry and `imprint skills fork|reset`; a user skill or a client-folder edit with a built-in's name counted as its fork; the release-change notice and its diff; the disable marker synced newest-first and skipped by older devices; built-ins never exported; every shipped built-in valid and named after its folder; the release archive keeping them.
- The other registry, registry-files, skills, sync and API test modules (930 tests on the branch before it joined `dev`), and Playwright `skills.spec.ts`, `tours.spec.ts` and `page-tours.spec.ts` (54 tests, including forking a built-in and the release-change notice), on Linux. The Skills page was checked in light and dark from screenshots.
- Linux (WSL Ubuntu, Python 3.12 venv): the full suite, 1,522 tests, passed on 2026-11-27 with the Board and built-in skills together.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): `test_builtin_skills`, `test_registry`, `test_registry_files` and `test_skills_tool` with the Board's tests, 187 tests, passed.
- Not run: macOS.
