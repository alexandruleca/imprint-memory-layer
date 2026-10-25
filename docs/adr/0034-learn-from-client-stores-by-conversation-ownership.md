---
title: ADR 0034 — Learn from client stores by conversation ownership
description: Learning reads the clients' own stores without an approved root; recorded working directories decide which workspace a conversation belongs to.
---

- Status: Amended
- Date: 2026-10-24
- Amends: [ADR 0015](0015-workspace-roots-and-source-purge.md) — supersedes its conversation-learning and desktop-export rules
- Amended by:
  - [ADR 0037](0037-learn-every-integrated-client-store.md) — more stores (`CLAUDE_CONFIG_DIR`, Codex sessions, registered WSL profiles) and Codex's session and turn working directories as recorded cwds
  - [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) — a Cursor folder name counts as a recorded cwd when exactly one existing directory has it
- Related: [ADR 0026](0026-windows-installer-wsl-client-bridge.md); [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md) and [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md), which read recorded cwds from Cline manifests and Copilot session events under the unchanged rule
- Restructured: 2026-10-25

## Context

ADR 0015 made approved roots the gate for learning as well as for file ingestion. `imprint learn` refused to start unless the active workspace had a root ("Approve this workspace's project/export roots first"). Each conversation was imported only when every recorded `cwd` lay inside the active workspace's roots, and desktop exports were read only from inside approved roots. On a fresh macOS or Windows install the default workspace has no roots, so `learn`, the dashboard's **Learn** action and Stop-hook capture did nothing until the user approved a directory they had never chosen. Desktop exports required approving the Downloads folder as a project root, which also made Downloads ingestible and claimable like a project.

Learning has a different source from ingestion. `imprint ingest <dir>` reads a directory the caller names, so approving that directory controls what is read. Learning reads fixed stores that Imprint's client integrations define: Claude Code's `~/.claude/projects` transcripts and auto-memory files, and the Downloads folders where Claude and ChatGPT exports land. No caller supplies those paths. What learning still has to decide is which workspace a conversation belongs to, so that conversations about one workspace's projects do not leak into another.

## Decision

Learning reads only its integrations' stores and needs no approved root to do so:

- `imprint learn` (and the dashboard action that queues it) no longer checks for roots before it starts.
- Desktop export scans read the default Downloads folders without a root. They read export zips in the folder itself; a link pointing elsewhere is skipped. Folders the caller adds (`--path`, or `paths` in `POST /api/desktop-learn/scan`) are the caller's choice and must lie inside the workspace's approved roots. Otherwise the scan fails with a visible error before reading anything.

One ownership rule, `workspace_policy.transcript_allowed`, decides whether a conversation belongs to a workspace. It is used by `learn`, Claude Code auto-memory import, the single-transcript import that the Stop hook runs, and the WSL bridge's Stop check:

- Every recorded `cwd` must lie inside this workspace's roots, or outside every other workspace's roots.
- A conversation about a project that another workspace claimed stays out, including a conversation that also touched an unclaimed directory.
- A conversation that no workspace has claimed is learned by the workspace that runs the learning. Roots shared on purpose (confirmed overlap) share their conversations.
- A conversation without recorded `cwd` evidence belongs to no workspace and is skipped. The encoded Claude folder name is still never used as evidence.

Roots remain the gate for filesystem ingestion and refresh, exactly as in ADR 0015. Learning never adds or claims a root.

## Rejected alternatives

- **Keep requiring roots for learning (ADR 0015).** This blocks every new install and makes users approve directories, including Downloads, that are not projects.
- **A flag that skips the check.** This leaves the wrong rule in place and turns an isolation rule into an opt-out.
- **Import every conversation into the active workspace.** This breaks workspace isolation for projects that another workspace explicitly owns.
- **Learn unclaimed conversations only while the workspace has no roots.** This adds a second mode whose result depends on unrelated roots, and adding a first root would silently stop learning other projects.
- **Route each conversation to the workspace that owns its directory, writing to several workspaces in one run.** This keeps more isolation, but it moves writes outside the workspace the user is acting in and needs multi-collection writes. It can follow later if users want it.

## Consequences

Learning works on a fresh install with the default workspace and no roots. Workspaces that own projects through roots keep their conversations. A workspace with roots now also receives conversations from directories that no workspace claimed. Users who want those kept out can approve the directories into the workspace that should own them.

Stop-hook capture follows the same rule. Sessions in unclaimed directories are captured into the hook's workspace instead of being skipped. On native installs the Stop hook applies the rule to conversation import only. Its extraction and summarization steps were already not gated by roots (the WSL bridge gates all three). That gap predates this decision and is unchanged.

Desktop exports no longer require approving Downloads. Exports keep their per-workspace import tracking, so each workspace imports an export once when it runs the scan.

## Invalidation conditions

- If users want each conversation routed to the workspace that owns its directory within one run, the alternative deferred here, revisit the rule that the learning workspace takes unclaimed conversations.
- If workspaces become bound per client session, the default to the workspace that runs the learning should follow that binding.

## Validation

- Python: `tests/test_workspace_policy.py` covers ownership with no roots, own and other workspaces' roots, mixed conversations, confirmed overlap and missing `cwd`. It also covers Downloads scanning without roots, skipping a linked zip, rejecting an unapproved extra folder and accepting it once approved, and that learning adds no root. `tests/test_wsl_bridge.py` checks that WSL `cwd` values are translated before the ownership check. The full suite passed on Linux (232 tests).
- The ownership and root tests also passed under native Windows Python 3.12.8 (Windows temporary paths, case-insensitive path comparison).
- Go: `go build ./...`, `go vet ./cmd/` and `go test ./...` passed on Linux.
- Not run: `imprint learn` end to end on macOS or Windows with real Claude Code transcripts, and the dashboard **Learn** action on those platforms.

Cursor follow-up: [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) counts a Cursor folder name as a recorded cwd when exactly one existing directory has it. Cursor's records state none. Claude Code's folder names are still never evidence.

Store coverage follow-up: [ADR 0037](0037-learn-every-integrated-client-store.md) extends the stores this rule is applied to (`CLAUDE_CONFIG_DIR`, Codex sessions, registered WSL profiles) and counts Codex's session and turn working directories as recorded cwds.

Cline follow-up: [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md) counts a Cline session manifest's `cwd` and `workspace_root` as recorded cwds. A session without a manifest has no evidence.

Copilot follow-up: [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md) counts the `cwd` of a Copilot session's `session.start`, `session.resume` and `session.context_changed` events as recorded cwds.
