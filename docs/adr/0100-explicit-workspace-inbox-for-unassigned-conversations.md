---
title: ADR 0100 — Explicit workspace inbox for unassigned conversations
description: Users may select one workspace to ingest known client conversations that have no usable directory evidence.
---

- Status: Accepted
- Date: 2026-10-28
- Amends:
  - [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) — an explicitly chosen inbox is the exception to skipping conversations without cwd evidence
  - [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) — unresolved Cursor folders can enter the explicit inbox
  - [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) — directory-free conversations have an explicit optional destination independent of session workspace selection
- Related: [ADR 0099](0099-sources-can-belong-to-several-projects.md)

## Context

Cursor conversations from empty windows, deleted directories or ambiguous folder names can have no resolvable cwd. Existing learning skips these before project analysis could inspect them. Semantic project inference cannot safely establish workspace ownership.

## Decision

Add `associations.unassigned_workspace`, default Off. Its nonempty value must name a known workspace; only that workspace may learn client conversations with no cwd evidence. The choice is explicit in Settings and does not follow whichever workspace happens to be active. All existing evidence checks remain: malformed/relative cwd or any conflicting workspace claimant rejects the conversation before inbox fallback. Known client-store discovery, explicit transcript imports and exclusions retain their existing boundaries.

New directory-free conversations use primary project `unassigned`. Existing source identity is retained on refresh. Project suggestions run within that workspace, independently of inbox ownership. Removing a workspace resets a locally configured inbox pointing at it.

## Rejected alternatives

- Infer a workspace from conversation similarity: semantic matches are not authorization to import into another workspace.
- Follow the active workspace automatically: a later workspace switch could import the same directory-free conversation elsewhere without an explicit destination.

## Consequences

Existing installations keep skipping directory-free conversations until the user selects a destination. Selecting an inbox enables imports on the next learning run; it does not run ingestion itself or move already imported data. Changing the destination affects future learning and does not erase prior imports. An inbox does not approve filesystem roots.

## Invalidation conditions

If clients supply reliable explicit workspace metadata, consider it through the ownership policy rather than semantic association. If users need multiple directory-free inboxes, require explicit per-client or per-source routing before relaxing the single destination.

## Validation

Linux/WSL temporary-profile tests check default rejection, one explicit destination, cross-workspace rejection, conflicting and relative cwd rejection, and a directory-free transcript entering `unassigned`. Native Windows/macOS client-store ingestion with this option remains unverified.
