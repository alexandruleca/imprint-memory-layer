---
title: ADR 0124 — OpenCode is a client; its sessions are exported from its database and learned when idle
description: Setup registers Imprint in OpenCode's global opencode.json with a listed instructions file and a plugin that learns idle sessions; learning exports each OpenCode session from its SQLite database to a transcript in Imprint's data directory.
---

- Status: Accepted
- Date: 2026-11-02
- Amends: [ADR 0037](0037-learn-every-integrated-client-store.md) — OpenCode's data directory is a learn store, read through exported transcripts
- Related: [ADR 0053](0053-connect-clients-without-touching-the-runtime.md) (the client table), [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md) (the last client added the same way), [ADR 0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md) (the detached learner the plugin starts), [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) (ownership by recorded working directory)

## Context

OpenCode (opencode.ai, `anomalyco/opencode`) is a terminal coding agent with MCP support. Imprint had no entry for it: setup did not register it and `imprint learn` did not read its conversations.

OpenCode resolves its directories from XDG base directories under the home on every platform: configuration in `$XDG_CONFIG_HOME/opencode` (default `~/.config/opencode`, overridden by `OPENCODE_CONFIG_DIR`) and data in `$XDG_DATA_HOME/opencode` (default `~/.local/share/opencode`). Its global `opencode.json` declares MCP servers under `"mcp"` as `{"type": "local", "command": [...], "environment": {...}, "enabled": true}` and extra rule files under `"instructions"`. Global rules otherwise come from `~/.config/opencode/AGENTS.md`, and only when that file does not exist from `~/.claude/CLAUDE.md`. Plugins are JavaScript modules in `plugins/` that receive bus events; a session going idle is `session.status` with status `idle` in OpenCode 2 and `session.idle` (deprecated, still sent) before.

Since 1.2.0 (February 2026) OpenCode stores sessions in a SQLite database, `opencode.db` in its data directory (`opencode-<channel>.db` for other release channels, or the file `OPENCODE_DB` names); the JSON tree under `storage/` is no longer read or written. OpenCode 2 (current release 2.2.5) keeps sessions in `session_v2`, messages in `session_message` (a `type` per row: `user`, `assistant`, `synthetic`, `system`, `skill`, `shell`, `compaction`, …) and an assistant's text, reasoning and tool calls in `session_part`; 1.2 to 1.x keep `session`, `message` (data with a `role`) and `part`. These were read from OpenCode's `packages/core/src/session/sql.ts` and `packages/schema/src/session-message.ts` on `dev` and at `v1.19.5`.

Imprint's learning reads transcripts as files: `transcripts.records`, `cwds`, `session`, `conversation_source.prepare`, source fingerprints and `session_learn` all take a path.

## Decision

`opencode` is a client in the client table. Setup and connect, when OpenCode's configuration or data directory exists or `opencode` is on `PATH`:

- register a local MCP server `imprint` under `"mcp"` in the global `opencode.json`, keeping the user's other entries and fields;
- write `imprint.md` (Imprint's instructions, naming the search tool `imprint_search` as OpenCode does) beside it and list its path in `"instructions"`; `AGENTS.md` is not written;
- write `plugins/imprint.js`, which on an idle session pipes `{"session_id", "cwd"}` to `hook_entry.py opencode-idle`, at most once every five seconds per session.

`hook_entry.py opencode-idle` accepts only an OpenCode session id (`ses_` and letters or digits), pins the workspace of the cwd as the Stop hook does, and starts `session_learn --opencode <id>` detached. The learner waits and takes the queue lock as ADR 0077 describes, then exports that session and learns the export.

Learning exports each top-level session (one without a parent) of every database in the data directory to `<data>/opencode_sessions/<hash of the database path>/<session id>.jsonl`: a first record stating the session id, its directory as the working directory, its title and database, then one record per message with a role (`user`, `assistant`, `context` for what OpenCode wrote itself, `tool` for a shell command the user ran) and content blocks in the shapes the reader already knows (text, reasoning, `tool_use`, `tool_result`). `transcripts` reads these records; everything after it is unchanged. A session is exported again only when its `time_updated` changes, so an unchanged session keeps its file's modification time and learning skips it; exports of deleted sessions are removed. Databases are opened read-only.

Disconnect removes the MCP entry, the `"instructions"` entry, `imprint.md` while it is the generated text, and the plugin.

## Rejected alternatives

- **Writing Imprint's rules into OpenCode's `AGENTS.md`, as for Codex.** Creating that file stops OpenCode from reading `~/.claude/CLAUDE.md`, which many users rely on; the `"instructions"` list adds a file without changing which global rules apply.
- **Reading sessions straight from the database through a virtual path (`opencode.db#ses_…`).** Every reader, the fingerprint, the source snapshot and the learner's slot take a file path; teaching each of them a second kind of source would touch most of the learning pipeline for one client.
- **Reading the legacy JSON tree under `storage/`.** OpenCode no longer writes it, and stopped importing it on 2026-06-02.
- **Learning subagent sessions.** No other client's subagent transcripts are learned; their parent session holds the conversation.
- **A WSL registration target.** Not needed for a first version; a WSL profile's OpenCode can be registered when someone uses it.

## Consequences

- OpenCode appears in `imprint clients`, the dashboard's client list, `connect`/`disconnect` and `imprint learn`.
- Imprint's data directory holds a copy of each learned OpenCode session as JSONL (files `0600`). The copy is unredacted, as OpenCode's own database is; what is stored as memories is redacted as for every client.
- A session is learned about a minute after it goes idle, as Claude Code's Stop hook does; without the plugin (an older OpenCode that does not load `plugins/`), `imprint learn` or the dashboard's Learn action still learns it.
- A schema change in OpenCode can make a session export with fewer messages; the reader ignores message and part types it does not know.

## Invalidation conditions

- If OpenCode records its conversations as files again, or offers an export command or API that gives the same content, then reading those replaces the database export.
- If OpenCode renames or reshapes `session_v2`, `session_message` or `session_part`, then the exporter must follow.
- If OpenCode stops reading the `"instructions"` list or the `plugins/` directory, then registration must move to what it reads.

## Validation

- `tests/test_opencode_sessions.py`: OpenCode 2 and 1.x databases built from the tables above export to transcripts whose session id, working directory and user/assistant text `transcripts` reads; synthetic messages and parts become context, idle markers and subagent sessions are left out; an unchanged session keeps its export's modification time and a deleted one loses its export; a single session exports by id and a foreign id is refused.
- `cmd/setup_opencode_test.go`: setup is skipped without OpenCode; with it, it registers the local server, lists the instructions file after the user's, writes the plugin, writes no `AGENTS.md`, is idempotent and counts as connected; disconnect removes exactly what it added.
- `cmd/learn_test.go`: OpenCode's data directory is one of this computer's learn stores.
- Not yet run against a live OpenCode installation.
