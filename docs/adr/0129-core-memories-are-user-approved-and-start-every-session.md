---
title: ADR 0129 — Core memories are user-approved and start every session
description: Proposed core memories, short statements of the user's preferences and standing instructions that the user approves, kept apart from ordinary memories and given to every agent session at its start.
---

- Status: Proposed
- Date: 2026-11-04
- Related: [ADR 0021](0021-budgeted-mcp-evidence-and-selective-expansion.md), [ADR 0037](0037-learn-every-integrated-client-store.md), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md), [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md), [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md), [ADR 0130](0130-reorganization-is-proposed-by-a-background-job-and-reviewed.md)

## Context

The user asked for a "main core memories" collection: their preferences and the things they like an agent to do, kept apart from everything else.

Nothing like it exists, and what comes closest does not reach a session:

- **The `preference` type** is set by regular expressions ("i prefer", "don't like", `imprint/classifier.py`) or by the LLM tagger. It is one type among many, and retagging can change it (ADR 0067's rejected alternatives).
- **Nothing from memory starts a session.** The SessionStart hook adds a fixed reminder and the session's workspace only (`imprint/hook_entry.py`, `session_context`). `wake_up`, whose digest weights preferences highly, runs only on a session's first `search` with `detail="full"`. The default compact search returns before it (`imprint/server.py`, ADR 0021).
- **Claude Code's own memory files are imported as ordinary notes.** Each file in `~/.claude/projects/*/memory` becomes a `decision` note. An edited file adds a new note beside the old one (`imprint/cli_conversations.py`, `learn_auto_memory`).
- **Retrieved text is untrusted.** The server's instructions tell agents to treat retrieved documents as reference material, not instructions (`imprint/server.py`). That is right for indexed content, which anyone's file or transcript may have written. It is also why ordinary memories cannot simply be handed to an agent as rules.

## Decision

A **core memory** is one short statement about the user that the user has approved, for example "Commit only when asked" or "Prefers concise answers that lead with the result". Each has:

- a kind: preference, instruction or fact about the user;
- a scope: everywhere, or one project;
- the evidence it came from: the memory ids and verbatim quotes of a proposal, or "written by the user";
- a state: proposed, active or retired;
- when it was approved, and on which device.

Core memories live in their own store, outside every workspace's collection. They describe the user, not a project's content, so they apply in every workspace. Only an approval in the dashboard makes one active:

- **Agents can propose, never approve.** A `store` with type `preference`, or a new MCP tool, adds a proposal.
- **The background job of ADR 0130 proposes them** from preference-typed memories, imported client memory files and conversation turns in which the user states how they want things done. Each proposal needs a verbatim quote, as ADR 0099 requires of model suggestions.
- **The user can write one directly** on a **Core memories** page, which also edits, scopes and retires them.

The SessionStart hook adds the active core memories for the session's project and the global ones, within a fixed budget (proposed: 2,000 characters), under a heading that says the user approved them. Because the user approved each one, they are instructions, unlike retrieved documents, and the server's instructions say so. Core memories sync to paired devices as their own record kind, carrying their state, so an approval or retirement made on one device holds on all of them.

## Rejected alternatives

- **A `core` flag on ordinary memories, or a `memories_core` Qdrant collection.** Core memories are few, edited by hand and handed over whole, not searched by similarity. Qdrant records are immutable apart from classification (ADR 0051), so editing one would mean a new memory id each time.
- **Injecting preference-typed memories automatically.** The type comes from regular expressions and an LLM, and those memories are untrusted text. A quoted third-party instruction would become a standing rule.
- **Attaching core memories to search results.** ADR 0021 kept compact search free of session context. Session start is the one moment every client already gives Imprint.

## Consequences

- Every session starts with the user's approved rules, in every client with a SessionStart hook. Codex, Copilot and Cursor take hooks differently, so a client without one gets them through an MCP tool or its instructions file.
- The user curates a short list instead of hoping the right memory ranks first.
- It needs a new store, a sync record kind with deletion-free state changes, a dashboard page, a hook change and changes to the server's instructions. Each is a decision this record makes for core memories only.
- A long list costs every session tokens. The budget keeps it bounded, and the page shows how much of it is used.

## Invalidation conditions

- If users rarely approve proposals, drop proposals and keep the hand-written list.
- If clients gain a standard way to receive user rules (for example a shared rules file every client reads), write core memories there instead of through hooks.
- If sync gains general mutable records, store core memories as those.

## Validation

None yet; this is a proposal. Before acceptance: a prototype hook on this repository's sessions with a hand-written list, and a measured candidate run of ADR 0130 over the maintainer's memories counting proposals worth approving.
