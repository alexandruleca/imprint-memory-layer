---
title: ADR 0171 — Personas are written for Codex and Cursor
description: Personas go into the fewest of the connected clients' subagent folders that reach them all, as Markdown for Claude Code and Cursor and as a TOML custom agent for Codex, whose edits come back into the persona.
---

- Status: Accepted
- Date: 2026-11-10
- Amends: [ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md) — personas reach Codex and Cursor, not only Claude Code
- Related: [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) (the registry), [ADR 0170](0170-skills-and-personas-reach-every-mcp-client-through-imprints-server.md) (the MCP route), [ADR 0053](0053-connect-clients-without-touching-the-runtime.md) (the client table), [skills](../skills.md)

## Context

[ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md) wrote personas only to Claude Code's `~/.claude/agents`, because no other client's subagent format had been checked. On 2026-11-10 the user asked for them in Codex and Cursor too. Their documentation that day:

| | Codex | Cursor |
| --- | --- | --- |
| Page | developers.openai.com/codex/subagents (now served at learn.chatgpt.com/docs/agent-configuration/subagents) | cursor.com/docs/subagents |
| User folder | `~/.codex/agents/`, project `.codex/agents/` | `~/.cursor/agents/`, and "for compatibility" `~/.claude/agents/` and `~/.codex/agents/`; the same for projects |
| File | One standalone TOML file per agent | One Markdown file with YAML front matter |
| Fields | `name`, `description` and `developer_instructions`, all required; `model`, `model_reasoning_effort` and other optional settings | `name` (default: from the file name), `description`, `model`, `readonly`, `is_background`; the body is the prompt |
| Name | "the `name` field is the source of truth"; matching the file name is "the simplest convention" | "Use lowercase letters and hyphens" |
| Use | Spawned "after a direct request or applicable project or skill instruction" | The agent "proactively delegates" by description, or `/name` |
| Same name | A custom agent overrides a built-in one | "`.cursor/` takes precedence over `.claude/` or `.codex/`" |

## Decision

**Folders.** The client table lists each client's persona folders, preferred first, and how they hold one:

- Claude Code: `~/.claude/agents`.
- Cursor: `~/.cursor/agents`, then `~/.claude/agents`.
- Codex: `~/.codex/agents` (under `CODEX_HOME` when set), in Codex's format.

`imprint clients --json` reports them as `personas` and `persona_format`. Personas go into the fewest of these folders that reach every connected client, by the rule skills already follow. With Claude Code and Codex connected, that is `~/.claude/agents` and `~/.codex/agents`, and Cursor reads the first. Cursor alone gets `~/.cursor/agents`. A client whose format this runtime does not know gets no persona folder.

**A persona in Codex's folder** is `<name>.toml` with the persona's name, its description on one line, and its instructions as `developer_instructions`. Settings meant only for Codex ride in the persona's front matter under `codex:` and are written as top-level keys. Only text, numbers and true/false carry over, because Claude Code's `model: sonnet` is not a Codex model. The folder's manifest records the file as written, so a second pass changes nothing.

**Edits in Codex come back.** A changed TOML file is read back into the persona: name, description, instructions and the `codex:` settings. The rest of the front matter (Claude Code's `tools`, say) comes from the persona. When only the instructions changed, the front matter's text is kept as written. The file is then rewritten in Imprint's layout. A file that is not valid TOML, lacks a required field, or sets a table such as `[mcp_servers]` or `[features]` is left in place and reported until it is fixed, as ADR 0169 does for an invalid edit.

**Found and adopted.** Codex agent files Imprint did not write are listed with the other found items and can be adopted. An adopted file that already holds the persona stays as the user wrote it and is managed from then on.

## Rejected alternatives

- **Counting on Cursor to read `~/.codex/agents`.** Cursor's page lists the folder for compatibility but describes only Markdown files, so it is not known to parse Codex's TOML there. Cursor gets a Markdown folder of its own.
- **Every client's own folder.** With Claude Code and Cursor both connected, `~/.cursor/agents` would repeat each persona that Cursor already reads in `~/.claude/agents`. Cursor's rule settles a repeated name, but each persona would be written and kept twice for nothing.
- **Translating Claude Code's fields into Codex's.** Model names and tool lists differ between the vendors. Only settings the user marks for Codex carry over.
- **Keeping Codex's TOML as a second file of the persona.** A persona is one file ([ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md)), and two copies of the instructions would drift apart.

## Consequences

- Codex lists the user's personas as custom agents and spawns one when asked. Cursor delegates to them by description, through `~/.claude/agents` when Claude Code is connected and its own folder otherwise.
- Cursor reads the Markdown as written for Claude Code. How Cursor treats a Claude Code value it does not know, such as `model: sonnet`, was not checked.
- A Codex agent whose name has an underscore, as in Codex's examples (`pr_explorer`), cannot be adopted: item names are lower-case letters, digits and hyphens. It is listed with that problem.
- Nested Codex settings (tables) are not carried, and a file that sets them is not taken back.
- The Skills page and `imprint skills` show the Codex folder like any other. The report marks its format.

## Invalidation conditions

- If Codex reads Markdown agents, or Claude Code's folder, write Markdown for it and drop the TOML rendering.
- If Cursor documents reading Codex's TOML agents in `~/.codex/agents`, count that folder among Cursor's.
- If users need nested Codex settings on a persona, carry tables in the `codex:` mapping.
- If another connected client's subagent folder and format are documented, add it to the client table.

## Validation

- `tests/test_registry_formats.py` (6 tests) and `tests/test_registry_files.py` (17 tests, 4 of them new), run on Windows 11 with the Python 3.13 runtime:
  - Instructions with quotes, triple quotes, backslashes, control characters, tabs, CRLF and non-ASCII text survive the TOML round trip.
  - Only scalar `codex:` settings are written.
  - The fewest persona folders are chosen for several sets of clients, and a format the runtime does not know is ignored.
  - A persona is written as TOML for Codex and Markdown for Claude Code and Cursor, and a second pass writes nothing.
  - An edit in Codex reaches the persona and Claude Code's copy, keeping `tools`, and an edit of the instructions alone keeps the front matter's text.
  - A table in the TOML is left in place and reported.
  - A Codex agent is found, adopted and left as written, and a name with an underscore is reported.
- `cmd/clients_test.go` with Go 1.27.3 on Linux, and `go vet` for Linux, Windows and macOS: the client table lists the documented persona folders and only Codex's format.
- Not verified: Codex and Cursor loading the written files, and Cursor's handling of a persona name repeated across its folders.
